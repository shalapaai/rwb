"""Все ORM-модели проекта одним импортом.

Внешние ключи между модулями (например, returns → orders) разрешаются, только когда
загружены модели всех модулей. Поэтому этот файл импортируют приложение (main.py),
Alembic (migrations/env.py) и тесты. Новый модуль с таблицами — добавить строку сюда.
"""

from app.modules.analytics import models as analytics  # noqa: F401
from app.modules.economics import models as economics  # noqa: F401
from app.modules.inventory import models as inventory  # noqa: F401
from app.modules.ledger import models as ledger  # noqa: F401
from app.modules.network import models as network  # noqa: F401
from app.modules.ops import models as ops  # noqa: F401
from app.modules.orders import models as orders  # noqa: F401
from app.modules.scenarios import models as scenarios  # noqa: F401
from app.modules.stubs import models as stubs  # noqa: F401
