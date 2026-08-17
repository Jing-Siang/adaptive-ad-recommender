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
| Change data capture | Kafka (KRaft) + Debezium — syncs campaign eligibility (status/budget/dates) from Postgres into Pinecone, see [`docs/kafka_cdc_plan.md`](docs/kafka_cdc_plan.md) |
| Agent framework | LangChain + MCP (`langchain-mcp-adapters`) |
| Backend | FastAPI |
| Frontend | React + Vite + TypeScript, Tailwind CSS, react-router-dom |
| Reliability | `tenacity`, Pydantic, atomic SQL budget updates |
| Testing | `pytest` (95 tests, 1 integration test run separately) |
| Deployment | Docker Compose locally → Railway/Render for production |

## Repo layout

```
adaptive-ad-recommender/
├── backend/
│   ├── app/
│   │   ├── core/       # shared infra: config, db, queue, embeddings, vector_store
│   │   ├── serving/    # users -> retrieve -> rerank -> guardrail -> serve -> events
│   │   └── campaigns/  # advertiser submits -> policy review -> moderation;
│   │                   #   pinecone_sync_consumer.py syncs Pinecone via Kafka CDC
│   ├── mcp_servers/    # ad-policy MCP resource server
│   ├── alembic/        # DB migrations
│   ├── data/            # synthetic personas + versioned seed campaign catalog
│   ├── tests/
│   └── scripts/         # feedback-round + demo-catalog-seeding demo artifacts
├── frontend/            # React + Vite + TS + Tailwind
│   └── src/
│       ├── pages/        # OnboardingFeedPage, PerformancePage, CampaignsPage, ModeratorPage
│       ├── components/    # Feed, FeedCard, OnboardingChat, ReactionButtons, CtrTrendChart, …
│       ├── api.ts         # fetch functions for every backend endpoint
│       └── types.ts       # TS interfaces mirroring the backend's Pydantic schemas
├── kafka/
│   └── connectors/      # Debezium Postgres connector registration JSON
├── docs/
│   ├── spec.md
│   └── kafka_cdc_plan.md   # Postgres -> Pinecone CDC sync design + phase-by-phase log
└── docker-compose.yml
```

## Local development

Common commands are wrapped in a `Makefile` — run `make help` for the full
list. `make` is Linux/macOS-native; on Windows, run it from WSL rather than
cmd.exe/PowerShell (the stack already assumes Docker + a Python venv +
bash-style commands, so WSL is the natural fit there too). Each step below
shows the `make` target alongside the raw command, for whichever you prefer.

### 1. Start Postgres + Redis

```bash
make infra
# equivalent to: docker compose up -d postgres redis
```

### 2. Backend setup

```bash
make install-backend
# equivalent to: cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

```bash
cd backend && cp .env.example .env   # fill in OPENAI_API_KEY, PINECONE_API_KEY
```

The Pinecone index itself isn't created automatically — create one (1536
dimensions, cosine metric, matching `text-embedding-3-small`) via the
Pinecone console or SDK before running anything that embeds/retrieves.

```bash
make migrate
# equivalent to: cd backend && source .venv/bin/activate && alembic upgrade head
```

Creates the schema (advertisers, campaigns, events, reactions).

### 3. Run the backend + worker (two processes)

```bash
# terminal 1
make backend
# equivalent to: cd backend && source .venv/bin/activate && uvicorn app.main:app --reload

# terminal 2
make worker
# equivalent to: cd backend && source .venv/bin/activate && rq worker --url redis://localhost:6379 campaign_review
```

The API runs at `http://localhost:8000` (interactive docs at `/docs`). The
worker is required — campaign review is async; without it, submitted
campaigns sit at `status=pending_review` forever.

### 4. Start Kafka + the Pinecone sync consumer (opt-in, but needed for approvals to actually serve)

```bash
make kafka                     # equivalent to: docker compose up -d kafka connect
make kafka-register-connector  # creates the compacted + DLQ topics, registers the Debezium connector (safe to re-run)

# terminal 3
make kafka-consumer
# equivalent to: cd backend && source .venv/bin/activate && python -m app.campaigns.pinecone_sync_consumer
```

A campaign approval no longer indexes into Pinecone synchronously — it's
picked up via Postgres change data capture (Debezium → Kafka) and applied
by this consumer, usually within a couple of seconds. **Skip this step and
an approved campaign will just never show up in recommendations, with
nothing telling you why.** See [`docs/kafka_cdc_plan.md`](docs/kafka_cdc_plan.md)
for the full design.

### 5. Try it

Every endpoint below requires a bearer token (see `docs/auth_plan.md` --
Google OAuth + JWT, no `user_id` request fields anywhere, it comes from
the token). The real login path is the frontend's Google Sign-In button;
for curl testing, mint a local token directly instead of going through a
real Google account:

```bash
cd backend && source .venv/bin/activate && python3 -c "
from app.core.db import SessionLocal
from app.core.auth import create_access_token
from app.models import User

db = SessionLocal()
user = db.query(User).filter_by(google_sub='demo-sub').first()
if user is None:
    user = User(google_sub='demo-sub', email='demo@example.com', display_name='Demo User', role='moderator')
    db.add(user)
    db.commit()
    db.refresh(user)
print(create_access_token(user))
"
# copy the printed token
export TOKEN='<paste it here>'
```

`moderator` is a superset role (see the role model above), so this one
token covers every example below.

```bash
# submit a campaign -- owned by whichever account $TOKEN belongs to, no
# separate advertiser field
curl -X POST localhost:8000/campaigns -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "headline": "24/7 Emergency Plumbing",
  "description": "Licensed plumbers near you. Free estimates.",
  "category": "home_repair",
  "objective": "conversions",
  "budget_total": 100.0,
  "start_date": "2026-01-01",
  "end_date": "2026-12-31"
}'
# -> status=pending_review; the worker picks it up and reviews it within a few seconds

# once the campaign's approved AND kafka-consumer has processed it (poll
# GET /campaigns?status=active, then check the consumer's own logs or just
# wait a couple seconds), the profile vector gets seeded the first time
# /onboarding/checkpoint decides to show candidates -- see
# app/serving/onboarding_api.py. Simplest way to get a recommendation
# without going through the full chat flow is to seed one directly:
curl -X POST localhost:8000/onboarding/checkpoint -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "messages": [{"role": "user", "content": "need a plumber for a leaky faucet"}]
}'

# feed-facing recommend: one call returns a ranked, guardrail-allowed batch
curl -X POST localhost:8000/recommend/batch -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "batch_size": 10
}'

# log an impression once a served ad actually scrolls into view
curl -X POST localhost:8000/events/impression -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "ad_id": "<served ad_id from above>"
}'

# react to the served ad -- like/dislike/interested; nudges the profile and
# (for like/interested) debits the campaign's budget
curl -X POST localhost:8000/events/reaction -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "ad_id": "<served ad_id from above>",
  "reaction": "like"
}'
```

If a campaign comes back `needs_review` instead of `active`/`rejected`,
resolve it as a moderator:

```bash
curl -X POST localhost:8000/campaigns/<id>/moderate -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{
  "outcome": "approved",
  "reason": "complies with policy",
  "reviewed_by": "your-name"
}'
```

### Tests

```bash
make test
# equivalent to: cd backend && source .venv/bin/activate && pytest -m "not integration"
```

Runs against the real dev Postgres (not SQLite — see `docs/spec.md` on why)
and mocks OpenAI/Pinecone/MCP at their boundaries; needs `make infra` running
first.

One test is excluded from `make test`: a real, unmocked end-to-end test of
the Kafka CDC sync (needs `make kafka` + `make kafka-register-connector`
running, and costs a tiny real OpenAI call). Run it explicitly:

```bash
make test-integration
```

### Frontend

```bash
cd frontend && cp .env.example .env
make install-frontend   # equivalent to: cd frontend && npm install
make frontend            # equivalent to: cd frontend && npm run dev
```

Runs at `http://localhost:5173`. For the onboarding chat / feed views to have
real candidates to draw from, seed the demo catalog first:

```bash
make seed
# equivalent to: cd backend && source .venv/bin/activate &&
#   python -m scripts.generate_seed_campaign_data && python -m scripts.seed_demo_campaigns
```

An empty catalog just means an empty feed, not a broken one.

### Everything, via Docker Compose

```bash
make docker-up
# equivalent to: docker compose up --build
```

Starts postgres, redis, the backend, the worker, and the frontend together
(still need to run `alembic upgrade head` and create the Pinecone index
once, per above). This also starts `kafka`/`connect` (they're regular
services in the same `docker-compose.yml`), but **not**
`pinecone_sync_consumer.py` — that's deliberately a native process, not a
docker-compose service (see [`docs/kafka_cdc_plan.md`](docs/kafka_cdc_plan.md)
for why), so it still needs `make kafka-register-connector` +
`make kafka-consumer` run separately even after `docker-up`.

## Status

Backend: fully built, tested (92 tests), and verified working end-to-end
against real Postgres/Redis/Pinecone/OpenAI — campaign posting/review,
ad serving/feedback, and the onboarding chat (see
[`docs/next_phase_plan.md`](docs/next_phase_plan.md) for design details).
Campaign eligibility (status/budget/dates) syncs from Postgres into
Pinecone via Kafka + Debezium CDC, not a synchronous write on approval —
see [`docs/kafka_cdc_plan.md`](docs/kafka_cdc_plan.md) for the full design,
live verification results, and load-test data.

Frontend: all four views built — onboarding chat + feed (View 1),
performance dashboard (View 2), campaign submission (View 3), and the
moderator queue (View 4). Verified against the real backend via scripted
integration tests hitting the actual endpoints in the same sequence the UI
calls them (no browser was available in the session that built it, so
rendering itself hasn't been visually confirmed yet — worth an eyeball pass
with `npm run dev` before treating it as done).
