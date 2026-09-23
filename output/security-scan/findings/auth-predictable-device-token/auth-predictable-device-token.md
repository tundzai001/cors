# Public device serial deterministically reveals the field-agent WebSocket credential

## Executive Summary

The field-agent WebSocket bearer credential is generated from the device serial
with a public, deterministic transformation. The same serial is returned by the
unauthenticated public map API. Once an administrator uses the auto-deployment
route, the backend stores the derived license value as `device.device_token`,
and `/ws/pi/{serial}` accepts that value as the credential for the device
identity. There is no random value or server-held secret anywhere in this
derivation.

An unauthenticated remote attacker can therefore collect a published station
serial, reproduce the transformation offline, and authenticate a WebSocket as
that field agent. A successful connection replaces the current WebSocket for
the same serial. The attacker can then submit fabricated device status and
telemetry and receive commands that the backend routes over that socket. The
`DEPLOY_LICENSE` command is explicitly WebSocket-first; other commands use the
WebSocket when MQTT is unavailable. Because the public map can expose many
serials, the weakness is potentially fleet-wide for devices provisioned through
the affected auto-deployment flow.

I reviewed source revision
`13546e7b992aefb3237e9e56d5e6c70c529db3ff` directly, corresponding to
snapshot `codex-security-snapshot/v1:sha256:32c72b8edad7b25495bf771955ed951c46bc67644dec918876744c2d10a6fad0`.
I also ran the bundled, network-free probe against synthetic identifiers to
confirm deterministic output and collisions. I did not start the application,
connect to a broker, station, caster, or production service, or exercise a live
WebSocket. No fixing revision, advisory identifier, CVE, or reliable
introduction date was available, so the affected-version claim is limited to
the reviewed revision.

This is primarily an insufficient-randomness weakness (CWE-330) that causes
improper authentication (CWE-287). Under the product threat model it merits
critical priority when auto-deployed tokens are used across the public station
fleet, because the device identity layer is reproducible without possessing a
credential.

## Background

The platform has two related concepts that the affected design collapses into
one value:

- a local license value, which the agent can validate against its machine
  serial; and
- a remote bearer credential, which the backend uses to authenticate the
  field-agent WebSocket.

The public station-map endpoint is intentionally unauthenticated. It queries all
devices and includes each mapped device's serial in its response:

```python
# cors_dashboard/backend/app/main.py:4546-4549, 4650-4657
@app.get("/api/public/map-stations")
async def get_public_map_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ...
    public_data.append({
        "serial": dev.serial,
        "name": dev.name,
        "status": dev.status,
```

Publishing a stable station identifier is not itself necessarily a flaw. The
security invariant is that knowledge of that identifier must not reveal a
credential. The threat model makes the same distinction: serials can be public,
while device tokens must be unpredictable, unique, and revocable.

An administrator with `MANAGE_LICENSE` can call the auto-deployment endpoint.
The endpoint computes a license base and key from the path serial, stores the
key in the device's `device_token` column, refreshes the authentication cache,
and sends the value to the agent:

```python
# cors_dashboard/backend/app/routers/devices.py:652-668, 679-694
@router.post("/api/devices/{serial}/auto-deploy-token")
async def auto_deploy_device_token(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(
        auth.require_permission(auth.Permission.MANAGE_LICENSE)
    )
):
    ...
    license_base = license_manager.generate_pi_license_base(serial)
    license_key = license_manager.get_license_code_from_string(license_base)
    ...
    device.device_token = license_key
    ...
    await mqtt_handler.cache_device_metadata(device)
    payload = {"command": "DEPLOY_LICENSE", "payload": {"license_key": license_key}}
    return await send_command_to_pi(serial, payload)
```

The permission check correctly restricts who may invoke this endpoint, but it
does not protect the resulting credential: an attacker does not need to call
the endpoint if the algorithm and its only input are already known.

On the agent, the deployed value is stored in `license.key`. The agent both
recomputes it locally from `MACHINE_SERIAL` and sends it as the WebSocket query
token (`geodetic/agent_universal.py:1075-1087` and `5274-5296`). That source
shape confirms that the same deterministic value is deliberately serving both
roles.

## Vulnerability Details

We first reach the root transformation in
`cors_dashboard/backend/app/license_manager.py`. The base generator takes only
the final ten serial characters, uppercases letters, concatenates decimal ASCII
code points, and then truncates or pads the result to twelve digits:

```python
# cors_dashboard/backend/app/license_manager.py:32-49
def generate_pi_license_base(serial_number: str) -> str:
    ...
    s_number_part = serial_number[-10:]
    ...
    for char in temp_str:
        base_code_str += str(ord(char))

    if len(base_code_str) > 12:
        return base_code_str[:12]
    return base_code_str.ljust(12, '0')
```

If we carry this twelve-digit string into
`get_license_code_from_string()` at lines 4-28, the function applies floating
point arithmetic, averaging, logarithms, and exponentiation. These operations
obscure the input but add no entropy. The output is a pure function of public
serial material. No CSPRNG, per-device random secret, server key, or enrollment
secret is consulted.

The reduction is also collision-prone. For common alphanumeric characters the
decimal code point occupies two digits, so truncating after twelve digits often
preserves only roughly the first six characters of the selected ten-character
suffix. Two identifiers with the same final ten characters necessarily collide,
and distinct final-ten-character values can also collide after truncation. The
offline PoC demonstrates both cases with concrete synthetic values.

Next we cross the authentication boundary. The backend resolves metadata for
the path serial, extracts the stored `device_token`, and compares the query
token to it:

```python
# cors_dashboard/backend/app/main.py:7948-7989
@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str,
                                token: str = Query(None)):
    enforce_auth = os.getenv("ENFORCE_AGENT_AUTH", "true").lower() != "false"
    metadata = await asyncio.wait_for(
        mqtt_handler.get_device_metadata(serial),
        timeout=float(os.getenv("AGENT_WS_AUTH_TIMEOUT_SECONDS", "3")),
    )
    expected_token = str(metadata.get("device_token") or "").strip()
    provided_token = str(token or "").strip()

    if enforce_auth and expected_token and provided_token != expected_token \
            and not license_bypass:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await pi_manager.connect(serial, websocket)
```

The default `ENFORCE_AGENT_AUTH=true` setting is not a useful countermeasure
here. We do not bypass the comparison; we predict the exact value it expects.
Likewise, rotating through `auto-deploy-token` recomputes the same value for the
same serial, so it does not invalidate knowledge of the credential.

Finally, `PiConnectionManager.connect()` installs the newly accepted socket in
the per-serial registry and closes the previous one:

```python
# cors_dashboard/backend/app/pi_websocket.py:13-25
async def connect(self, serial: str, websocket: WebSocket):
    await websocket.accept()
    previous = self.active_connections.get(serial)
    self.active_connections[serial] = websocket
    ...
    if previous and previous is not websocket:
        await asyncio.wait_for(
            previous.close(code=1012, reason="superseded by newer connection"),
            timeout=1.0,
        )
```

This gives the predicted token a concrete security effect. We become the
registered WebSocket for the victim serial, displace the legitimate secondary
channel, and can send `status_update` messages. The server binds the path serial
into those payloads and processes them as that device (`main.py:7991-8044`).
Commands sent through `PiConnectionManager.send_personal_message()` go to the
socket currently stored under that serial (`pi_websocket.py:39-60`).

## Exploitability Analysis

The strongest path has four modest preconditions:

1. The target device has a serial exposed by `/api/public/map-stations` or by
   another attacker-visible operational source.
2. An administrator has provisioned its `device_token` with the affected
   auto-deployment path.
3. The attacker can reach `/ws/pi/{serial}` at the deployed backend.
4. Per-device license bypass is not needed; the predicted value satisfies the
   normal comparison even with authentication enforcement enabled.

From there, we derive the token entirely offline. There is no brute force,
timing dependency, or interaction with a rate limiter. We carry the public
serial through the two pure helper functions and submit the resulting bearer
value for that same serial. If the stored token came from this route, equality
is deterministic.

The immediate primitive is field-agent identity takeover, not arbitrary backend
code execution. Once accepted, the attacker-controlled socket becomes the
registry entry for the serial. It can fabricate the JSON status stream consumed
by the backend and dashboard, and it can repeatedly supersede the legitimate
WebSocket as each side reconnects. This can mislead operators about device
health and geodetic telemetry and can disrupt the secondary control channel.

Command interception has an important routing constraint. In
`cors_dashboard/backend/app/command_dispatcher.py:11-28`, `DEPLOY_LICENSE` is
sent over the WebSocket first. Most other commands prefer MQTT and fall back to
WebSocket only when MQTT is unavailable. We can therefore reliably receive and
acknowledge WebSocket-first license deployment while registered; interception
of raw receiver commands, reset, configuration, and similar operations depends
on MQTT being unavailable or on the deployment using the WebSocket fallback.
The predicted token alone does not grant dashboard-user authority, broker
credentials, or a direct ability to write to the genuine station's serial port.

Several apparent defenses do not close this path:

- TLS would protect transport confidentiality but cannot make a deterministic
  public-input credential secret.
- The `MANAGE_LICENSE` permission protects the deployment action, not offline
  reproduction after deployment.
- Cache refresh correctly makes the newly stored token effective, but the new
  token equals the old derivation for the same serial.
- Closing the previous WebSocket actually makes identity takeover more visible
  in the source: the attacker's accepted socket replaces the genuine one.
- Collision handling cannot restore unpredictability. The model declares
  `device_token` as indexed and nullable rather than unique
  (`cors_dashboard/backend/app/models.py:9-16`), and even a uniqueness
  constraint would only reject duplicates; it would not hide the value.

Fleet scale depends on provisioning coverage. Devices with no public-map
coordinates may not appear in this endpoint, and devices using independently
random credentials would not be affected. Conversely, every publicly listed
serial whose stored token was produced by the affected route is independently
derivable. Static review establishes the full source/control/sink chain, but I
did not measure production token provenance or deployment coverage.

## Proof of Concept

The bundled PoC is intentionally a diagnostic offline probe rather than a live
WebSocket client. This matches the testing boundary: it never contacts a target
and uses only three synthetic identifiers. It copies the two relevant helper
functions closely enough to show the bad invariant directly.

Run it from the report directory:

```sh
cd poc
python derive_token.py
```

Representative output from my local execution is:

```text
Offline serial-derived token probe (synthetic identifiers only)
A: serial=LAB-A-00000000ABCDEF1234 base=656667686970 token=783222233545
B: serial=LAB-B-99999999ABCDEF1234 base=656667686970 token=783222233545
C: serial=LAB-C-00000000ABCDEF9999 base=656667686970 token=783222233545
deterministic_repeat=true
same_last_10_collision=true
different_last_10_truncation_collision=true
PASS: public identifier material alone determines the legacy bearer value
```

Serials A and B have the same final ten characters, so the first collision
demonstrates the suffix reduction. Serial C has a different final-ten-character
value, but its retained decimal-ordinal prefix is the same, demonstrating the
additional truncation collision. Repeating serial A produces the same base and
token byte-for-byte.

The values above are synthetic and are not usable against any known device.
The probe performs no network or file I/O, creates no persistent state, and
requires no cleanup. It does not claim that a particular production device was
provisioned with this algorithm. On a fixed implementation the historical probe
will still print the same legacy value; the product regression criterion is
that the actual WebSocket credential is independently random, differs across
rotations, and rejects this derived value.

## Remediation

The invariant to restore is simple: a device's remote authentication credential
must be independent of every public identifier and of the license calculation.
It must be generated from a cryptographically secure random source, unique per
device and per rotation, revocable, and compared only after the server has
confirmed that the device record and credential are present. License entitlement
and transport authentication should be separate values with separate storage
and lifecycle rules.

A minimal server-side shape would look like this:

```python
import hashlib
import hmac
import secrets

def issue_device_ws_credential() -> tuple[str, str]:
    plaintext = secrets.token_urlsafe(32)  # returned once for enrollment
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, digest

def verify_device_ws_credential(provided: str, stored_digest: str) -> bool:
    if not provided or not stored_digest:
        return False
    candidate = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, stored_digest)
```

The deployment route should generate a new plaintext credential, store only its
digest in a dedicated column, update the metadata cache atomically, transmit the
plaintext through an authenticated enrollment/rotation channel, and never
derive it from `serial`. The agent should store this credential separately from
`license.key` with service-account-only permissions. The WebSocket endpoint
should reject unknown devices, devices without an enrolled credential, missing
credentials, and mismatches. It should not place bearer secrets in a query
string; an authorization header, mutually authenticated TLS, or a constrained
WebSocket subprotocol avoids common URL logging. A stronger design uses a
per-device client certificate or asymmetric enrollment key so the backend does
not need to send a reusable bearer secret over an already established channel.

Rotation must also invalidate sessions, not only future handshakes. After the
new digest commits, close any socket authenticated with the old credential and
require a fresh handshake. Provide a one-time bootstrap mechanism for devices
that have not yet enrolled; do not fall back to accepting an empty token or the
legacy serial-derived value. During migration, mark legacy credentials and
expire them on a short, auditable schedule rather than silently accepting both
forever.

Regression coverage should include:

- two issuances for the same serial produce different high-entropy credentials;
- two different devices never share a credential in a large generated sample;
- the historical `derive_token(serial)` value is rejected;
- missing-token, empty-token, missing-device, and missing-server-digest
  handshakes fail closed;
- the correct credential is accepted only for its own serial;
- rotation rejects the old credential immediately and closes its existing
  socket;
- concurrent metadata lookup and rotation cannot restore the old digest;
- the public map contains no credential or credential-derived material;
- logs and access telemetry do not record plaintext credentials; and
- local license validation continues to work without being reused as remote
  authentication.

## Summary

At the reviewed revision, a public station serial is both the input to a
deterministic license calculation and, indirectly, the source of the bearer
credential accepted by the field-agent WebSocket. We can follow that value from
the unauthenticated map response, through the pure serial transformation and
privileged assignment route, into the WebSocket comparison and per-serial
connection registry. The offline probe confirms that identical public input
always yields identical output and that the lossy base conversion creates
additional collisions.

The result is remotely reproducible field-agent impersonation for every exposed
device provisioned with this flow, including replacement of the legitimate
WebSocket, fabricated device messages, and receipt of WebSocket-routed commands.
The fix is not to obscure or salt the existing arithmetic. It is to separate
licensing from authentication and issue cryptographically random, revocable
device credentials through a secure enrollment lifecycle. Future variant review
should examine all MQTT and WebSocket identity paths for other places where a
serial, license code, mountpoint, or other public operational identifier is
treated as proof of possession.

