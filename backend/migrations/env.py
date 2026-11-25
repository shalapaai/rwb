from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.db import Base

# Модели всех модулей уже импортированы — autogenerate видит их таблицы.
from app.modules.analytics import models as _analytics  # noqa: E402, F401
from app.modules.economics import models as _economics  # noqa: E402, F401
from app.modules.inventory import models as _inventory  # noqa: E402, F401
from app.modules.ledger import models as _ledger  # noqa: E402, F401
from app.modules.network import models as _network  # noqa: E402, F401
from app.modules.ops import models as _ops  # noqa: E402, F401
from app.modules.orders import models as _orders  # noqa: E402, F401
from app.modules.scenarios import models as _scenarios  # noqa: E402, F401
from app.modules.stubs import models as _stubs  # noqa: E402, F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
