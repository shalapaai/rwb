"""Утреннее сокращение mesh-полки.

Литровый лимит меняется вместе с назначением заданий. Дата следующего шага
сохраняется в ПВЗ и позволяет продолжить план после перезапуска.
"""

import sqlalchemy as sa
from alembic import op

revision = "b19f82a63d71"
down_revision = "cd52e84f9a24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("active_limit_l", sa.Numeric()))
    op.add_column("nodes", sa.Column("next_reduction_day", sa.Date()))
    op.add_column("units", sa.Column("reduction_on", sa.Date()))
    op.execute("""
        UPDATE nodes SET next_reduction_day = (now() AT TIME ZONE 'Europe/Moscow')::date
            + COALESCE((SELECT offset_days FROM sim_clock WHERE id=1),0) + 1,
            active_limit_l = LEAST(storage_total_l * mesh_share_pct / 100,
                GREATEST(storage_total_l * pending_share_pct / 100,
                    (SELECT COALESCE(SUM(p.volume_l::numeric),0)
                     FROM units u JOIN products p ON p.sku=u.sku WHERE u.node_id=nodes.id
                     AND u.status IN ('on_shelf','reserved','remove_pending'))))
        WHERE pending_share_pct IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("units", "reduction_on")
    op.drop_column("nodes", "next_reduction_day")
    op.drop_column("nodes", "active_limit_l")
