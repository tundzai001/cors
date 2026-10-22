"""Persist the caster mountpoint inventory alongside rover accounts.

Revision ID: 20261022_caster_sources
Revises: 20261018_user_mobile_base_access
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261022_caster_sources'
down_revision = '20261018_user_mobile_base_access'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'caster_sources',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('host', sa.String(), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('mountpoint', sa.String(), nullable=False),
        sa.Column('password', sa.String(), nullable=True),
        sa.Column('source_data', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('last_seen_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('host', 'port', 'mountpoint', name='uq_caster_sources_target'),
    )


def downgrade():
    op.drop_table('caster_sources')
