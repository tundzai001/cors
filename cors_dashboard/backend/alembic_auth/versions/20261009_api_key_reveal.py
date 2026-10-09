"""Store recoverable API keys encrypted; preserve legacy hashes."""
from alembic import op

revision = "20261009_api_key_reveal"
down_revision = "20261008_api_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS token_encrypted TEXT")


def downgrade():
    # Existing hashes keep working; only the ability to reveal saved keys is lost.
    op.drop_column("api_keys", "token_encrypted")
