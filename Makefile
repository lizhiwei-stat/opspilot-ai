# Common tasks. All Python commands run through uv; mock LLM mode by default.
COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: corpus corpus-docker eval eval-docker notebook e2e-rag install migrate seed dev-backend dev-frontend test test-unit lint fmt fe-build fe-test up down seed-docker ps e2e check

install:
	uv sync
	cd frontend && npm ci

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed_demo.py

dev-backend:
	uv run uvicorn opspilot.main:create_app --factory --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	OPSPILOT_REQUIRE_DB=1 uv run pytest -v

test-unit:
	uv run pytest tests/unit -v

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff check --fix . && uv run ruff format .

fe-build:
	cd frontend && npm run build

fe-test:
	cd frontend && npm test

up:
	docker compose --env-file .env -f infra/docker-compose.yml up -d --build

down:
	docker compose --env-file .env -f infra/docker-compose.yml down

check: lint test fe-build fe-test

seed-docker:
	$(COMPOSE) exec backend python scripts/seed_demo.py

ps:
	$(COMPOSE) ps

# Real-browser login test against the running stack (downloads Chromium into ~/.cache once).
e2e:
	uv run --no-project --with playwright==1.64.1 python -m playwright install chromium
	set -a; . ./.env; set +a; OPSPILOT_E2E_PASSWORD="$$OPSPILOT_SEED_ADMIN_PASSWORD" \
	  uv run --no-project --with playwright==1.64.1 python frontend/e2e/login_e2e.py \
	  --url http://localhost:$${OPSPILOT_FRONTEND_PORT:-5173} --email admin@opspilot.local --expect-role admin

# ---- Stage 2 (RAG) -------------------------------------------------------------------
# Ingest the synthetic corpus (idempotent; content-hash dedup) — host venv or container.
corpus:
	uv run python scripts/load_demo_corpus.py

corpus-docker:
	$(COMPOSE) exec backend python scripts/load_demo_corpus.py

# 4-mode retrieval eval, mock embedding (free). Writes evals/results/*.json|md + eval_runs rows.
eval:
	uv run python evals/run_retrieval_eval.py

eval-docker:
	$(COMPOSE) exec -e OPSPILOT_GIT_COMMIT=$$(git rev-parse HEAD) backend python evals/run_retrieval_eval.py

notebook:
	uv run python scripts/build_notebook.py
	uv run --group notebook jupyter nbconvert --to notebook --execute --inplace notebooks/rag_walkthrough.ipynb

e2e-rag:
	set -a; . ./.env; set +a; OPSPILOT_E2E_PASSWORD="$$OPSPILOT_SEED_USER_PASSWORD" \
	  uv run --no-project --with playwright==1.64.1 python frontend/e2e/rag_e2e.py \
	  --url http://localhost:$${OPSPILOT_FRONTEND_PORT:-5173} --email alice@opspilot.local
