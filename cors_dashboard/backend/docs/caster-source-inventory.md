# Saved caster mountpoints

Mountpoints now live in the auth database alongside rover accounts, in
`caster_sources`. Each row is unique by host, port and mountpoint. Source
passwords are stored separately and excluded from list responses.

## Rollout

From `cors_dashboard/backend`, using the backend Python environment:

```sh
python -m alembic -c alembic_auth.ini upgrade head
```

Apply the migration before starting the updated backend workers. Deploy the
backend and frontend assets together. No devices-database migration is needed.
Existing rover accounts and device configurations are unchanged.

The existing caster monitor imports mountpoints on its next polling cycle
(default 30 seconds). An administrator can also open **Rover & Caster → Các
mountpoint → Đồng bộ caster** to import immediately. Imports update existing
rows and add new mountpoints. Opening the list reads only saved data.

Failed or empty caster responses retain the inventory. Missing remote rows are
also retained: this is a saved inventory, not a transient connection list.
Explicitly deleting a mountpoint removes it from both caster and database after
the caster confirms success or confirms that the mountpoint is already absent. Creating a mountpoint saves the row after caster
confirmation. If the database write fails after a successful caster create,
the next import recovers that source. If a delete's database write fails, retry
the delete: confirmation that the remote row is absent also removes the saved
row. Automatic imports do not prune rows.

Polling and create/delete operations share a transaction advisory lock per
caster host/port, so an older polling snapshot cannot recreate a row deleted by
another worker. Successful imports preserve existing passwords if the caster
omits them. Credentials and settings already stored for other casters are not
modified by a failed import.

## Rollback

Roll back the application to its previous version before removing the table.
The previous API can read mountpoints directly from caster without this table.

```sh
python -m alembic -c alembic_auth.ini downgrade 20261018_user_mobile_base_access
```

Downgrade drops only the saved mountpoint inventory, including its saved source
passwords. It does not delete mountpoints on caster or alter rover accounts.
Reapplying the upgrade creates an empty inventory that can be imported again.
