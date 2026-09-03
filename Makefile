.PHONY: dev up down logs test test-unit test-integration lint format migrate seed
dev: up
up:
	docker compose up --build
down:
	docker compose down
logs:
	docker compose logs -f api worker web
test: test-unit test-integration
test-unit:
	pytest tests/unit tests/golden -q
test-integration:
	pytest tests/integration -q -m integration
lint:
	ruff check .
	mypy apps services packages demos
	cd apps/web && npm run lint && npm run typecheck
format:
	ruff check --fix .
	ruff format .
	cd apps/web && npx prettier --write .
migrate:
	alembic upgrade head
seed:
	python -m demos.support_agent.seed
	python -m demos.rag_research.seed
