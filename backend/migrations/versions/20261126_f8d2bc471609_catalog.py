"""Категории товаров и дневные продажи по ПВЗ.

Пустая категория допустима для карточек, созданных до миграции.
Демо-карточкам категорию назначит сид.
"""

import sqlalchemy as sa
from alembic import op

revision = "f8d2bc471609"
down_revision = "e681fa7c320b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("mesh_allowed", sa.Boolean(), nullable=False),
        sa.Column("ban_reason", sa.Text()),
        sa.Column("marked", sa.Boolean(), nullable=False),
    )
    op.add_column("products", sa.Column("category_id", sa.String(32)))
    op.create_foreign_key("fk_products_category", "products", "categories", ["category_id"], ["id"])
    op.create_table(
        "sales",
        sa.Column("sku", sa.String(32), sa.ForeignKey("products.sku"), primary_key=True),
        sa.Column("pvz_id", sa.String(32), sa.ForeignKey("nodes.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.CheckConstraint("qty > 0", name="sales_qty_positive"),
    )


def downgrade() -> None:
    op.drop_table("sales")
    op.drop_constraint("fk_products_category", "products", type_="foreignkey")
    op.drop_column("products", "category_id")
    op.drop_table("categories")
