.PHONY: install dev test lint fmt typecheck migrate seed docker-up docker-down

# ── Setup ──────────────────────────────────────────────────────────────────
install:
	uv sync

# ── Development ────────────────────────────────────────────────────────────
dev:
	uv run uvicorn axiom.main:app --reload --host 0.0.0.0 --port 8000

worker:
	uv run arq axiom.workers.tasks.WorkerSettings

# ── Quality ────────────────────────────────────────────────────────────────
test:
	uv run pytest -v --cov=axiom --cov-report=term-missing

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy src/axiom --ignore-missing-imports

# ── Database ───────────────────────────────────────────────────────────────
migrate:
	uv run alembic upgrade head

migrate-gen:
	uv run alembic revision --autogenerate -m "$(msg)"

migrate-down:
	uv run alembic downgrade -1

seed:
	uv run python -m scripts.seed

# ── Docker ─────────────────────────────────────────────────────────────────
docker-up:
	docker compose up -d --build

docker-down:
	docker compose down -v

docker-logs:
	docker compose logs -f api worker
