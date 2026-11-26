"""Дневной учёт фактических отправок при сокращении mesh-полки.

В журнал попадают только подтверждённые возвраты по плану уменьшения доли.
Штрихкод представлен уникальным идентификатором физической единицы.
"""

import sqlalchemy as sa
from alembic import op

revision = "cd52e84f9a24"
down_revision = "a731c20e1bd4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reduction_dispatches",
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), primary_key=True),
        sa.Column("pvz_id", sa.String(32), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("volume_l", sa.Numeric(), nullable=False),
    )
    op.create_index("ix_reduction_pvz_day", "reduction_dispatches", ["pvz_id", "day"])

    # Старые задания сокращения удерживали единицы от продажи.
    op.execute("""
        UPDATE units u SET status = 'on_shelf', version = version + 1
        FROM nodes n WHERE n.id = u.node_id AND n.pending_share_pct IS NOT NULL
          AND u.status = 'remove_pending'
    """)
    # Уже освобождённые полки больше не требуют повторного сохранения доли.
    # В той же операции восстанавливаем задания обычной очереди, если она есть.
    op.execute("""
        WITH ready AS (
          UPDATE nodes n SET mesh_share_pct = pending_share_pct, pending_share_pct = NULL
          WHERE pending_share_pct IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM units u WHERE u.node_id=n.id AND status='awaiting_return')
            AND (SELECT COALESCE(SUM(p.volume_l::numeric), 0) FROM units u
                 JOIN products p ON p.sku=u.sku WHERE u.node_id=n.id
                 AND u.status IN ('reserved','on_shelf'))
                <= n.storage_total_l * n.pending_share_pct / 100
          RETURNING id, storage_total_l * mesh_share_pct / 100 AS capacity
        ), queue AS (
          SELECT u.id, u.status,
             SUM(p.volume_l::numeric) OVER (PARTITION BY u.node_id) - r.capacity AS excess,
             COALESCE(SUM(p.volume_l::numeric) FILTER (WHERE u.status='on_shelf')
                 OVER (PARTITION BY u.node_id ORDER BY u.shelved_at NULLS LAST,u.id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS older
          FROM units u JOIN ready r ON r.id=u.node_id JOIN products p ON p.sku=u.sku
          WHERE u.status IN ('on_shelf','reserved','awaiting_shelf')
        )
        UPDATE units u SET status='remove_pending', version=version+1 FROM queue q
        WHERE q.id=u.id AND q.status='on_shelf' AND q.older < q.excess
    """)


def downgrade() -> None:
    op.drop_table("reduction_dispatches")
