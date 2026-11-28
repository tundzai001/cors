"""Import the pinned 63-province SQL release into an isolated PostgreSQL schema."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit, unquote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'app/data/legacy_v2_4_1'
SCHEMA = 'vn_admin_legacy'
HASHES = {
    'CreateTables_vn_units.sql': '8be47315ef6f1fad1c03ad01f16b41d9b8a4bea08d6017f330a09d5795f7c6f9',
    'ImportData_vn_units.sql': 'd18d1ee8bc8f57401a45c3d345952be3ab3b36a7813b1ca7a01c5f9d21e2af25',
}


def source_sql():
    sources = []
    for name, expected in HASHES.items():
        data = (SOURCE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('SQL source checksum mismatch: ' + name)
        sources.append(data.decode('utf-8'))
    return '\n'.join(sources)


def import_sql(snapshot):
    guards = []
    for table, digest in snapshot.items():
        guards.append(f"""IF (SELECT md5(coalesce(string_agg(row_to_json(t)::text, '' ORDER BY code), ''))
            FROM public.{table} t) <> '{digest}' THEN
            RAISE EXCEPTION 'Current {table} changed; aborting legacy import'; END IF;""")
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '90s';
SELECT pg_advisory_xact_lock(hashtext('vn_admin_legacy_v2.4.1'));
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
SET LOCAL search_path = {SCHEMA}, pg_catalog;
{source_sql()}
CREATE TABLE vn_provinces_metadata (
    dataset_version text PRIMARY KEY,
    source_url text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO vn_provinces_metadata(dataset_version,source_url) VALUES
('v2.4.1','https://github.com/thanglequoc/vietnamese-provinces-database/tree/v2.4.1');
DO $$ BEGIN
IF (SELECT count(*) FROM provinces) <> 63
 OR (SELECT count(*) FROM districts) <> 696
 OR (SELECT count(*) FROM wards) <> 10035 THEN
 RAISE EXCEPTION 'Legacy row counts do not match the pinned release'; END IF;
{' '.join(guards)}
END $$;
COMMIT;
"""


def _psql_runner(command, env):
    def run(sql, write=False):
        child_env = dict(env)
        if not write:
            child_env['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=10000'
        else:
            child_env.pop('PGOPTIONS', None)
        result = subprocess.run(command, input=sql.encode('utf-8'), env=child_env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=100)
        if result.returncode:
            # Do not print connection strings or entire failing INSERT statements.
            raise RuntimeError('PostgreSQL command failed; transaction rolled back, exit=' + str(result.returncode))
        return result.stdout.decode('utf-8').strip()
    return run


def main(apply=False):
    values = dotenv_values(ROOT / '.env')
    url = urlsplit(os.getenv('DATABASE_URL') or values['DATABASE_URL'])
    executable = shutil.which('psql') or r'C:\Program Files\PostgreSQL\18\bin\psql.exe'
    env = os.environ.copy()
    env.update(PGPASSWORD=unquote(url.password or ''), PGCLIENTENCODING='UTF8', PGCONNECT_TIMEOUT='8')
    command = [executable, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
               '-h', url.hostname or 'localhost', '-p', str(url.port or 5432),
               '-U', unquote(url.username or ''), '-d', unquote(url.path.lstrip('/'))]
    run = _psql_runner(command, env)

    source_sql()
    snapshot = {table: run(f"SELECT md5(coalesce(string_agg(row_to_json(t)::text, '' ORDER BY code), '')) FROM public.{table} t;")
                for table in ('provinces', 'wards')}
    current = {table: int(run(f'SELECT count(*) FROM public.{table};')) for table in snapshot}
    exists = run(f"SELECT to_regclass('{SCHEMA}.vn_provinces_metadata') IS NOT NULL;") == 't'
    if exists:
        if run(f"SELECT dataset_version FROM {SCHEMA}.vn_provinces_metadata;") != 'v2.4.1':
            raise RuntimeError('Existing legacy schema has a different owner/version')
    elif apply:
        run(import_sql(snapshot), write=True)
        exists = True
    legacy = {table: int(run(f'SELECT count(*) FROM {SCHEMA}.{table};'))
              for table in ('provinces', 'districts', 'wards')} if exists else None
    if exists and list(legacy.values()) != [63, 696, 10035]:
        raise RuntimeError('Existing legacy schema is incomplete; no automatic overwrite')
    for table, digest in snapshot.items():
        if run(f"SELECT md5(coalesce(string_agg(row_to_json(t)::text, '' ORDER BY code), '')) FROM public.{table} t;") != digest:
            raise RuntimeError('Current dataset changed during verification')
    print(json.dumps({'current': current, 'legacy': legacy, 'current_checksums_unchanged': True,
                      'schema': SCHEMA, 'applied': apply}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Import only into the separate legacy schema')
    main(parser.parse_args().apply)
