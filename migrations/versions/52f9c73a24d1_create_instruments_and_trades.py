"""create instruments and trades

Revision ID: 52f9c73a24d1
Revises: 813a9b23aec3
Create Date: 2026-11-14 14:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '52f9c73a24d1'
down_revision: Union[str, Sequence[str], None] = '813a9b23aec3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'instruments',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('market', sa.String(length=20), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'utc')"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'utc')"), nullable=False),
        sa.CheckConstraint("market IN ('tw_stock', 'us_stock', 'fund', 'other')", name='ck_instruments_market'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('market', 'symbol', name='uq_instruments_market_symbol')
    )
    op.create_table(
        'trades',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('trade_type', sa.String(length=30), nullable=False),
        sa.Column('traded_on', sa.Date(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=16, scale=6), nullable=True),
        sa.Column('price', sa.Numeric(precision=16, scale=6), nullable=True),
        sa.Column('cash_amount', sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column('ratio', sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column('fee', sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column('tax', sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('fx_rate_to_twd', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'utc')"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'utc')"), nullable=False),
        sa.CheckConstraint(
            "trade_type IN ('buy', 'sell', 'cash_dividend', 'stock_dividend', 'drip', 'split', 'merge', 'capital_reduction')",
            name='ck_trades_trade_type'
        ),
        sa.CheckConstraint('(fee IS NULL) OR (fee >= 0)', name='ck_trades_fee_non_negative'),
        sa.CheckConstraint('(tax IS NULL) OR (tax >= 0)', name='ck_trades_tax_non_negative'),
        sa.CheckConstraint('fx_rate_to_twd > 0', name='ck_trades_fx_rate_to_twd_positive'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('trades')
    op.drop_table('instruments')
