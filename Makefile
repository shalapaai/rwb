.DEFAULT_GOAL := help
COMPOSE := docker compose

help: ## Список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Поднять всё в Docker
	$(COMPOSE) up --build

down: ## Остановить контейнеры
	$(COMPOSE) down

reset-db: ## Удалить данные БД
	$(COMPOSE) down -v

test: ## Тесты backend в контейнере
	$(COMPOSE) run --rm backend pytest -q

lint: ## Линтер backend в контейнере
	$(COMPOSE) run --rm backend ruff check .

migration: ## Новая миграция: make migration m="описание"
	$(COMPOSE) run --rm backend alembic revision --autogenerate -m "$(m)"

migrate: ## Применить миграции
	$(COMPOSE) run --rm backend alembic upgrade head

backup: ## Дамп БД в backups/
	mkdir -p backups
	$(COMPOSE) exec -T db pg_dump -U mesh mesh > backups/mesh_$$(date +%Y%m%d_%H%M%S).sql

restore: ## Восстановить: make restore f=backups/файл.sql
	$(COMPOSE) exec -T db psql -U mesh mesh < $(f)

backend-dev: ## Backend локально (нужен venv и docker compose up db)
	cd backend && uvicorn app.main:app --reload

frontend-dev: ## Frontend локально
	cd frontend && npm run dev

.PHONY: help up down reset-db test lint migration migrate backup restore backend-dev frontend-dev
