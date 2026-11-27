"""Persist Zalo assistant state and retire operator role.

Revision ID: 20260704_zalo_smart
Revises: b274d596f264
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260704_zalo_smart"
down_revision: Union[str, Sequence[str], None] = "b274d596f264"
branch_labels = None
depends_on = None
USER_ID_FOREIGN_KEY = "users.id"


def upgrade() -> None:
    op.execute(
        "UPDATE users SET role = 'coordinator', can_create_rovers = FALSE, "
        "can_create_users = FALSE WHERE role = 'operator'"
    )

    op.create_table(
        "zalo_chat_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("chat_type", sa.String(), nullable=False, server_default="private"),
        sa.Column("binding_type", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(USER_ID_FOREIGN_KEY, ondelete="CASCADE"), nullable=True),
        sa.Column("sender_id", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("registered_by", sa.String(), nullable=True),
        sa.UniqueConstraint("chat_id", name="uq_zalo_chat_binding_chat_id"),
    )
    op.create_index("ix_zalo_chat_bindings_chat_id", "zalo_chat_bindings", ["chat_id"])
    op.create_index("ix_zalo_chat_bindings_binding_type", "zalo_chat_bindings", ["binding_type"])
    op.create_index("ix_zalo_chat_bindings_user_id", "zalo_chat_bindings", ["user_id"])
    op.create_index("ix_zalo_chat_bindings_sender_id", "zalo_chat_bindings", ["sender_id"])
    op.create_index("ix_zalo_chat_bindings_is_active", "zalo_chat_bindings", ["is_active"])

    op.create_table(
        "zalo_link_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(USER_ID_FOREIGN_KEY, ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("used_at", sa.BigInteger(), nullable=True),
        sa.Column("used_by_chat_id", sa.String(), nullable=True),
    )
    op.create_index("ix_zalo_link_codes_user_id", "zalo_link_codes", ["user_id"])
    op.create_index("ix_zalo_link_codes_code_hash", "zalo_link_codes", ["code_hash"])
    op.create_index("ix_zalo_link_codes_expires_at", "zalo_link_codes", ["expires_at"])

    op.create_table(
        "zalo_workflow_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("sender_id", sa.String(), nullable=False),
        sa.Column("flow", sa.String(), nullable=False),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("state_encrypted", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("chat_id", "sender_id", name="uq_zalo_workflow_actor"),
    )
    op.create_index("ix_zalo_workflow_states_flow", "zalo_workflow_states", ["flow"])
    op.create_index("ix_zalo_workflow_states_expires_at", "zalo_workflow_states", ["expires_at"])

    op.create_table(
        "zalo_pending_actions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("code_hash", sa.String(), nullable=False, unique=True),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("sender_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(USER_ID_FOREIGN_KEY, ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False, server_default="standard"),
        sa.Column("target_identifier", sa.String(), nullable=True),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("state_fingerprint", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("executed_at", sa.BigInteger(), nullable=True),
    )
    for column in ("code_hash", "chat_id", "sender_id", "user_id", "action", "status", "expires_at"):
        op.create_index(f"ix_zalo_pending_actions_{column}", "zalo_pending_actions", [column])

    op.create_table(
        "zalo_notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("zalo_chat_bindings.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("min_severity", sa.String(), nullable=False, server_default="critical"),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("quiet_start", sa.String(), nullable=True),
        sa.Column("quiet_end", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="Asia/Saigon"),
        sa.Column("critical_override", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "zalo_delivery_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("zalo_chat_bindings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("binding_id", "event_key", name="uq_zalo_delivery_event"),
    )
    op.create_index("ix_zalo_delivery_logs_binding_id", "zalo_delivery_logs", ["binding_id"])
    op.create_index("ix_zalo_delivery_logs_status", "zalo_delivery_logs", ["status"])


def downgrade() -> None:
    op.drop_table("zalo_delivery_logs")
    op.drop_table("zalo_notification_preferences")
    op.drop_table("zalo_pending_actions")
    op.drop_table("zalo_workflow_states")
    op.drop_table("zalo_link_codes")
    op.drop_table("zalo_chat_bindings")

