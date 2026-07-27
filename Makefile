.PHONY: help install install-backend install-frontend infra infra-down migrate \
	backend worker frontend seed test docker-up docker-down

help:
	@echo "adaptive-ad-recommender -- common dev commands"
	@echo ""
	@echo "  make install        Install backend (venv) + frontend deps"
	@echo "  make infra          Start Postgres + Redis (docker compose)"
	@echo "  make infra-down     Stop Postgres + Redis"
	@echo "  make migrate        Run Alembic migrations (backend)"
	@echo "  make backend        Run the API (uvicorn --reload) -- keep running in its own terminal"
	@echo "  make worker         Run the RQ campaign-review worker -- keep running in its own terminal"
	@echo "  make frontend       Run the Vite dev server -- keep running in its own terminal"
	@echo "  make seed           Generate + seed the demo campaign catalog"
	@echo "  make test           Run backend tests (needs infra running)"
	@echo "  make docker-up      Run everything (postgres, redis, backend, worker, frontend) via Docker Compose"
	@echo "  make docker-down    Stop the Docker Compose stack"
	@echo ""
	@echo "Typical flow: make infra, then make backend / make worker / make frontend in three terminals."

install: install-backend install-frontend

install-backend:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

infra:
	docker compose up -d postgres redis

infra-down:
	docker compose stop postgres redis

migrate:
	cd backend && . .venv/bin/activate && alembic upgrade head

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload

worker:
	cd backend && . .venv/bin/activate && rq worker --url redis://localhost:6379 campaign_review

frontend:
	cd frontend && npm run dev

seed:
	cd backend && . .venv/bin/activate && python -m scripts.generate_seed_campaign_data && python -m scripts.seed_demo_campaigns

test:
	cd backend && . .venv/bin/activate && pytest

docker-up:
	docker compose up --build

docker-down:
	docker compose down
