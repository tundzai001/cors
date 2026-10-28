"""Add first-party page analytics without modifying existing session data."""
from alembic import op
ANALYTICS_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS analytics_page_views (
        view_id VARCHAR(36) PRIMARY KEY, visitor_id VARCHAR(36) NOT NULL,
        path VARCHAR(300) NOT NULL, device VARCHAR(30) NOT NULL,
        os VARCHAR(60) NOT NULL, browser VARCHAR(30) NOT NULL,
        created_at BIGINT NOT NULL, last_seen_at BIGINT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_analytics_page_views_visitor_id ON analytics_page_views(visitor_id)",
    "CREATE INDEX IF NOT EXISTS ix_analytics_page_views_created_at ON analytics_page_views(created_at)",
    "CREATE INDEX IF NOT EXISTS ix_analytics_page_views_last_seen_at ON analytics_page_views(last_seen_at)",
)

revision = '20261028_analytics'
down_revision = '20261022_caster_sources'
branch_labels = None
depends_on = None


def upgrade():
    for statement in ANALYTICS_SCHEMA_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table('analytics_page_views')
