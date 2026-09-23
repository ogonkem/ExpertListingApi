.PHONY: up down reset logs migrate seed test lint fmt

up:
	docker compose up -d --build

down:
	docker compose down

# Drops the db volume too, so docker/initdb scripts re-run on next `make up`.
reset:
	docker compose down -v

logs:
	docker compose logs -f

migrate:
	docker compose exec api alembic upgrade head

# e.g. make seed ARGS="--count 2000 --reset"
seed:
	docker compose exec api python -m scripts.seed $(ARGS)

test:
	uv run pytest -q --cov --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app scripts alembic tests

fmt:
	uv run ruff format .
	uv run ruff check . --fix
