"""Очередь возвратов при заполнении mesh-полки.

Расширяем допустимые статусы единиц. Откат возможен после выполнения заданий,
чтобы ожидающие размещения товары не потерялись при смене версии.
"""

import sqlalchemy as sa
from alembic import op

revision = "a731c20e1bd4"
down_revision = "ce794c1e8ec0"
branch_labels = None
depends_on = None

old = ("on_shelf", "reserved", "in_transit", "ready", "issued", "to_hub", "at_hub")


def upgrade() -> None:
    op.add_column("nodes", sa.Column("pending_share_pct", sa.Integer(), nullable=True))
    op.drop_constraint("status_valid", "units", type_="check")
    op.create_check_constraint(
        "status_valid", "units", f"status IN {(*old, 'remove_pending', 'awaiting_shelf', 'awaiting_return')}"
    )


def downgrade() -> None:
    op.drop_constraint("status_valid", "units", type_="check")
    op.create_check_constraint("status_valid", "units", f"status IN {old}")
    op.drop_column("nodes", "pending_share_pct")
