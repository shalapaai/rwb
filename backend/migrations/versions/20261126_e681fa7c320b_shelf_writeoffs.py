"""Журнал списаний по упаковке и объёму за день стенда.

Переносим прежние отказы и подтверждённые возвраты неподходящих по объёму
товаров. День определяем с учётом истории переключений тестовых часов.
"""

import sqlalchemy as sa
from alembic import op

revision = "e681fa7c320b"
down_revision = "d354914da25a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shelf_writeoffs",
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), primary_key=True),
        sa.Column("pvz_id", sa.String(32), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("category IN ('packaging', 'volume')", name="category_valid"),
    )
    op.create_index("ix_shelf_writeoffs_pvz_day", "shelf_writeoffs", ["pvz_id", "day"])
    op.execute("""
        WITH events AS (
            SELECT unit_id, pvz_id, created_at,
                   CASE WHEN grade = 'C' THEN 'packaging' ELSE 'volume' END AS category,
                   reason
            FROM returns WHERE kind = 'refusal' AND decision = 'hub'
              AND (grade = 'C' OR reason LIKE 'объём товара превышает лимит всей mesh-полки%')
            UNION ALL
            SELECT r.unit_id, r.pvz_id, k.created_at, 'volume',
                   'Объём товара превышает лимит всей полки'
            FROM idempotency_keys k
            JOIN returns r ON r.unit_id = (k.response->>'unit_id')::integer
            WHERE k.key LIKE 'shelf:%' AND split_part(k.key, ':', 3) = 'return'
              AND k.response->>'status' = 'to_hub' AND r.kind = 'refusal'
        )
        INSERT INTO shelf_writeoffs (unit_id, pvz_id, day, category, reason)
        SELECT e.unit_id, e.pvz_id,
               (LEAST(e.created_at, c.base_at) AT TIME ZONE 'Europe/Moscow')::date
                 + COALESCE((
                     SELECT (k.response->>'offset_days')::integer FROM idempotency_keys k
                     WHERE (k.key LIKE 'sim-next:%' OR k.key LIKE 'sim-clock:%')
                       AND k.created_at <= e.created_at
                     ORDER BY k.created_at DESC LIMIT 1
                 ), 0),
               e.category, e.reason
        FROM events e CROSS JOIN sim_clock c
    """)


def downgrade() -> None:
    op.drop_table("shelf_writeoffs")
