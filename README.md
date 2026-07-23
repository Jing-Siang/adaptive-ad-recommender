# Adaptive Ad Recommender

Two halves: advertisers **post** campaigns, which go through an AI (and
sometimes human) policy review before they're eligible to serve; the system
then **serves** the most relevant eligible campaign to a user based on
their profile/context, and learns from feedback over time — nudging user
profiles and debiting campaign budgets as it goes.

See [`docs/spec.md`](docs/spec.md) for the full design.

## Stack

| Layer | Tool |
|---|---|
| LLM (reasoning, re-ranking, structured output) | OpenAI API (`gpt-4o-mini`, Responses API) |
| Embeddings | OpenAI (`text-embedding-3-small`) |
| Vector store | Pinecone (serverless) |
| Relational store | Postgres + SQLAlchemy + Alembic |
| Async job queue | Redis + RQ |
| Agent framework | LangChain + MCP (`langchain-mcp-adapters`) |
| Backend | FastAPI |
| Frontend | React + Vite + TypeScript |
| Reliability | `tenacity`, Pydantic, atomic SQL budget updates |
| Testing | `pytest` (68 tests) |
| Deployment | Docker Compose locally → Railway/Render for production |

## Repo layout

```
adaptive-ad-recommender/
├── backend/
│   ├── app/
│   │   ├── core/       # shared infra: config, db, queue, embeddings, vector_store
│   │   ├── serving/    # users -> retrieve -> rerank -> guardrail -> serve -> events
│   │   └── campaigns/  # advertiser submits -> policy review -> moderation
│   ├── mcp_servers/    # ad-policy MCP resource server
│   ├── alembic/        # DB migrations
│   ├── data/            # synthetic personas + versioned seed campaign catalog
│   ├── tests/
│   └── scripts/         # feedback-round + demo-catalog-seeding demo artifacts
├── frontend/            # React + Vite + TS (placeholder, see Status)
│   └── src/
├── docs/
│   └── spec.md
└── docker-compose.yml
```

## Local development

### 1. Start Postgres + Redis

```bash
docker compose up -d postgres redis
```

### 2. Backend setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY, PINECONE_API_KEY
alembic upgrade head    # creates the advertisers/campaigns tables
```

The Pinecone index itself isn't created automatically — create one (1536
dimensions, cosine metric, matching `text-embedding-3-small`) via the
Pinecone console or SDK before running anything that embeds/retrieves.

### 3. Run the backend + worker (two processes)

```bash
# terminal 1
uvicorn app.main:app --reload
# terminal 2
rq worker --url redis://localhost:6379 campaign_review
```

The API runs at `http://localhost:8000` (interactive docs at `/docs`). The
worker is required — campaign review is async; without it, submitted
campaigns sit at `status=pending_review` forever.

### 4. Try it

```bash
# submit a campaign
curl -X POST localhost:8000/campaigns -H 'Content-Type: application/json' -d '{
  "advertiser_name": "Acme Plumbing Co",
  "headline": "24/7 Emergency Plumbing",
  "description": "Licensed plumbers near you. Free estimates.",
  "category": "home_repair",
  "objective": "conversions",
  "budget_total": 100.0,
  "start_date": "2026-01-01",
  "end_date": "2026-12-31"
}'
# -> status=pending_review; the worker picks it up and reviews it within a few seconds

# no accounts/login -- a user is just a caller-supplied user_id. Create a
# profile once (this is what retrieve/recommend reads back, it never embeds
# raw text on its own):
curl -X POST localhost:8000/users -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user-1",
  "interest_summary": "need a plumber for a leaky faucet"
}'

# once the campaign's approved (poll GET /campaigns/{id} or check the worker's
# logs), get a recommendation
curl -X POST localhost:8000/recommend -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user-1",
  "top_k": 5
}'

# feed-facing version: one call returns a ranked batch instead of one ad
curl -X POST localhost:8000/recommend/batch -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user-1",
  "batch_size": 10
}'

# log an impression once a served ad actually scrolls into view
curl -X POST localhost:8000/events/impression -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user-1",
  "ad_id": "<served ad_id from above>"
}'

# react to the served ad -- like/dislike/interested; nudges the profile and
# (for like/interested) debits the campaign's budget
curl -X POST localhost:8000/events/reaction -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user-1",
  "ad_id": "<served ad_id from above>",
  "reaction": "like"
}'
```

If a campaign comes back `needs_review` instead of `active`/`rejected`,
resolve it as a moderator:

```bash
curl -X POST localhost:8000/campaigns/<id>/moderate -H 'Content-Type: application/json' -d '{
  "outcome": "approved",
  "reason": "complies with policy",
  "reviewed_by": "your-name"
}'
```

### Tests

```bash
cd backend && source .venv/bin/activate && pytest
```

Runs against the real dev Postgres (not SQLite — see `docs/spec.md` on why)
and mocks OpenAI/Pinecone/MCP at their boundaries; needs `docker compose up
-d postgres redis` running first.

### Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Runs at `http://localhost:5173`.

### Everything, via Docker Compose

```bash
docker compose up --build
```

Starts postgres, redis, the backend, the worker, and the frontend together
(still need to run `alembic upgrade head` and create the Pinecone index
once, per above).

## Status

Backend: both pipelines (campaign posting/review and ad serving/feedback)
are built, tested (68 tests), and verified working end-to-end against real
Postgres/Redis/Pinecone/OpenAI. See [`docs/next_phase_plan.md`](docs/next_phase_plan.md)
for what's still in progress (the onboarding chat). Frontend is
still a placeholder — no recommendation dashboard,
decision-trace viewer, CTR chart, or moderator queue page yet; the system is
currently only usable via the API directly (see the curl examples above).
