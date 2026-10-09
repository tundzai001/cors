"""Persist admin host policy without changing whole-device lock state."""
from alembic import op
revision = '20261009_station_host_lock'
down_revision = '20261001_unique_rtcm_client_leases'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('ALTER TABLE devices ADD COLUMN IF NOT EXISTS server_config_lock JSONB')

def downgrade():
    op.execute('ALTER TABLE devices DROP COLUMN IF EXISTS server_config_lock')
