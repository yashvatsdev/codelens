"""create analysis_snapshots table

Revision ID: a4f3e1b2c9d8
Revises: 31a841f72f4f
Create Date: 2026-09-27 04:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f3e1b2c9d8'
down_revision: Union[str, Sequence[str], None] = '31a841f72f4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the analysis_snapshots table."""
    op.create_table(
        'analysis_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column(
            'scanned_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('total_findings', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errors', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('warnings', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('info', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_analysis_snapshots_repository_id'),
        'analysis_snapshots',
        ['repository_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_analysis_snapshots_scanned_at'),
        'analysis_snapshots',
        ['scanned_at'],
        unique=False,
    )


def downgrade() -> None:
    """Drop the analysis_snapshots table."""
    op.drop_index(
        op.f('ix_analysis_snapshots_scanned_at'),
        table_name='analysis_snapshots',
    )
    op.drop_index(
        op.f('ix_analysis_snapshots_repository_id'),
        table_name='analysis_snapshots',
    )
    op.drop_table('analysis_snapshots')
