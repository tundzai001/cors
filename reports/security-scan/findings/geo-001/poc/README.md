# Offline MQTT credential-pattern inventory probe

This proof of concept checks the two affected Python sources for hard-coded
MQTT password assignments and fallback defaults. It is intentionally limited
to local source inspection:

- it uses only Python's standard library;
- it does not import an MQTT client;
- it does not open sockets, resolve hosts, or authenticate anywhere;
- it never prints usernames, passwords, broker addresses, or secret hashes;
- it does not modify the source checkout.

## Requirements

- Python 3.9 or newer
- A checkout containing `geodetic/agent_universal.py` and
  `geodetic/geotek_daithanh_aitogy.py`
- Git only when `--revision` is supplied

## Run against the reviewed revision

Place the `geo-001` report directory beside a checkout named `cors`:

```text
bundle/
|-- geo-001/
|   `-- poc/
`-- cors/
```

Then run:

```sh
cd geo-001/poc
python inventory_probe.py --repo ../../cors \
  --revision 13546e7b992aefb3237e9e56d5e6c70c529db3ff
```

To inspect the checkout's current files rather than a Git revision:

```sh
cd geo-001/poc
python inventory_probe.py --repo ../../cors
```

## Expected vulnerable output

```text
[!] hard-coded MQTT password/default: geodetic/agent_universal.py:322 value=<MASKED>
[!] hard-coded MQTT password/default: geodetic/geotek_daithanh_aitogy.py:93 value=<MASKED>
[!] hard-coded MQTT password/default: geodetic/geotek_daithanh_aitogy.py:351 value=<MASKED>
[!] one MQTT password literal repeats 3 times across 2 files
[!] vulnerable credential pattern present (3 occurrences)
```

The process exits with status 2 when it finds at least one literal password or
password default. A corrected source tree should produce:

```text
[+] no literal MQTT password/default found in the reviewed agent sources
```

and exit with status 0.

Parse or repository errors use status 1. The probe changes no files, so no
cleanup is required.

## What this proves—and what it does not

The probe proves that credential material is embedded in source and whether an
identical literal is reused across files. It masks every value by construction.

It does not test whether any credential is active, whether a broker is
reachable, which ACLs are assigned, whether a command can be published, or
whether a device is online. Those checks require a separately authorized test
broker or deployment audit and must not be performed with the exposed value.

