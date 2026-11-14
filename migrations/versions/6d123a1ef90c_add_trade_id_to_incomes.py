"""add trade_id to incomes

Revision ID: 6d123a1ef90c
Revises: 52f9c73a24d1
Create Date: 2026-11-14 14:58:30.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6d123a1ef90c'
down_revision: Union[str, Sequence[str], None] = '52f9c73a24d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('incomes', sa.Column('trade_id', sa.BigInteger(), nullable=True))
    op.create_unique_constraint('uq_incomes_trade_id', 'incomes', ['trade_id'])
    op.create_foreign_key(
        'fk_incomes_trade_id_trades',
        'incomes',
        'trades',
        ['trade_id'],
        ['id'],
        ondelete='RESTRICT'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_incomes_trade_id_trades', 'incomes', type_='foreignkey')
    op.drop_constraint('uq_incomes_trade_id', 'incomes', type_='unique')
    op.drop_column('incomes', 'trade_id')
