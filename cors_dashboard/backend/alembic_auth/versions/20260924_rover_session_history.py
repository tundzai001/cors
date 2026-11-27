"""Persist rover connection sessions and their latest complete position."""

from typing import Sequence

from alembic import op


revision: str = "20260924_rover_session_history"
down_revision: str | Sequence[str] | None = "20260912_rover_page_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runtime schema repair creates this table on startup, so make the formal
    # migration safe to run afterward as well.
    op.execute(
        """CREATE TABLE IF NOT EXISTS rover_session_history (
            id SERIAL PRIMARY KEY,
            username VARCHAR NOT NULL,
            server VARCHAR NOT NULL,
            mountpoint VARCHAR NOT NULL DEFAULT '',
            client_id VARCHAR NOT NULL DEFAULT '',
            receiver_id VARCHAR NOT NULL DEFAULT '',
            started_at BIGINT NULL,
            last_seen_at BIGINT NOT NULL,
            ended_at BIGINT NULL,
            uptime_seconds INTEGER NULL,
            latitude DOUBLE PRECISION NULL,
            longitude DOUBLE PRECISION NULL,
            position_updated_at BIGINT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rover_session_history_username ON rover_session_history (username)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rover_session_history_server ON rover_session_history (server)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rover_session_history_last_seen_at ON rover_session_history (last_seen_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rover_session_history_ended_at ON rover_session_history (ended_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rover_session_history_lookup "
        "ON rover_session_history (username, server, last_seen_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rover_session_history")
