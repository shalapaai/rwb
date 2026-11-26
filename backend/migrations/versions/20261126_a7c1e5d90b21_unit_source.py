"""Источник единицы: отказ, пополнение из хаба или поставка продавца (HB1).

Revision ID: a7c1e5d90b21
Revises: f8d2bc471609
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c1e5d90b21"
down_revision = "f8d2bc471609"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "units", sa.Column("source", sa.String(8), nullable=False, server_default="refusal")
    )
    op.create_check_constraint("source_valid", "units", "source IN ('refusal', 'hub', 'seller')")


def downgrade() -> None:
    op.drop_constraint("source_valid", "units", type_="check")
    op.drop_column("units", "source")
