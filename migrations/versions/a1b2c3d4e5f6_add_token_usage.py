"""add token_usage table

Revision ID: a1b2c3d4e5f6
Revises: cf44d51dd0dd
Create Date: 2026-09-14 00:00:00.000000

Adds the ``token_usage`` table used for Phase 6 real token accounting:
one row is recorded per model call (stream provider usage when available,
local estimates otherwise). The application also self-heals at runtime via
``db.init_db`` so existing deployments pick this up without running alembic.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'cf44d51dd0dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'token_usage',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.Column('estimated', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_token_usage_conversation_id'), 'token_usage', ['conversation_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_token_usage_conversation_id'), table_name='token_usage')
    op.drop_table('token_usage')