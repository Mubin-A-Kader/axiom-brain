.PHONY: install dev test lint fmt typecheck migrate seed docker-up docker-down

# ── Setup ──────────────────────────────────────────────────────────────────
install:
	pip install -e ".[dev]"

# ── Development ────────────────────────────────────────────────────────────
dev:
	uvicorn axiom.main:app --reload --host 0.0.0.0 --port 8000

worker:
	arq axiom.workers.tasks.WorkerSettings

# ── Quality ────────────────────────────────────────────────────────────────
test:
	pytest -v --cov=axiom --cov-report=term-missing

lint:
	ruff check src tests

fmt:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy src/axiom --ignore-missing-imports

# ── Database ───────────────────────────────────────────────────────────────
migrate:
	alembic upgrade head

migrate-gen:
	alembic revision --autogenerate -m "$(msg)"

migrate-down:
	alembic downgrade -1

seed:
	python -m scripts.seed

# ── Docker ─────────────────────────────────────────────────────────────────
docker-up:
	docker compose up -d --build

docker-down:
	docker compose down -v

docker-logs:
	docker compose logs -f api worker
