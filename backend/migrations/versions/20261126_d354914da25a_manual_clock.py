"""Ручной календарь стенда.

Фиксируем начальный момент один раз и сохраняем накопленный сдвиг дней.
Существующие планы продолжаются со следующего нажатия оператора.
"""

import sqlalchemy as sa
from alembic import op

revision = "d354914da25a"
down_revision = "b19f82a63d71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sim_clock", sa.Column(
        "base_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ))
    op.execute("INSERT INTO sim_clock (id, offset_days) VALUES (1, 0) ON CONFLICT DO NOTHING")
    op.execute("""
        UPDATE nodes SET next_reduction_day = (base_at AT TIME ZONE 'Europe/Moscow')::date
            + offset_days + 1
        FROM sim_clock WHERE nodes.pending_share_pct IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("sim_clock", "base_at")
