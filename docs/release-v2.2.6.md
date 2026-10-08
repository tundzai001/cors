# V2.2.6

## Service configuration

- The dashboard sends only changed fields. The backend uses `exclude_unset=True` and merges the patch into the saved configuration. V2.2.6+ agents normally receive the patch; older agents receive the merged configuration because they replace their services. Agents reporting missing caster settings receive the saved configuration to repair them.
- Missing fields keep their previous values. Empty or null passwords also keep the existing password. A nonempty password replaces it.
- Pi and LTE agents retain unrelated configuration when applying a patch.
- Admins can reveal saved passwords with the eye button; other roles retain replacement-only inputs.
- Caster 1/2 headers and host fields show enabled/disabled, confirmed connection and data rate, sleep waiting for a rover, offline, or unapplied changes. Connection status comes from agent telemetry.

A fresh online heartbeat stays online even with all caster outputs disabled. Caster availability is displayed separately. Out-of-sync telemetry cannot erase the saved settings in the dashboard, and partial status updates preserve saved config fields in the database.

## Upgrade

1. Update and restart both universal Pi and relay/LTE agents to V2.2.6 **before editing service settings**. The backend now protects older Pi agents by sending the complete merged configuration to them.
2. Update the backend/frontend, set `APP_VERSION=V2.2.6` in the backend environment, and restart the backend. An existing environment value overrides the new code default.
3. If the API key migration from the previous release has not run, execute from `cors_dashboard/backend`:

   ```sh
   python -m alembic -c alembic_auth.ini upgrade head
   python -m alembic -c alembic_auth.ini current
   ```

   The auth database head is `20261008_api_keys`. V2.2.6 itself adds no database migration.

4. For an out-of-sync station whose caster settings are still saved in the backend, open Services and click Save and Apply, even without edits, to restore the saved settings to the agent. If the saved host/port/mountpoint fields are also missing, restore them from a known configuration or backup.

5. A password already overwritten with an empty value cannot be reconstructed by this fix. Re-enter the correct password for affected stations, then verify caster authentication and wake-up with a rover.

## Verification

Regression checks cover backend patch/save separation, stored password preservation, Pi and LTE partial deployment, and explicit password replacement. Browser checks cover admin visibility controls, patch-only submission, redacted realtime updates, non-admin masking, connection/sleep/offline indicators, and mobile layout. Live caster authentication and rover wake-up require deployment and have not been verified locally.
