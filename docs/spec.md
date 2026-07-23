# Contextual Ad Recommendation Engine — Project Spec

## Overview

An end-to-end AI engineering system with two halves: advertisers **post**
campaigns, which go through an AI (and sometimes human) policy review before
they're eligible to serve; the system then **serves** the most relevant
eligible campaign to a user based on their profile/context, and learns from
feedback over time. Built with production-grade reliability (real
persistence, async processing, retries, structured logging), not a notebook
demo.

**Goal:** combine RAG-style retrieval, LLM reasoning/re-ranking, agentic
tool use (MCP), and LLMOps practices into a single cohesive system.

**Why this project:** it can't be solved with a single LLM prompt. It
requires persistent state (campaigns, budgets, evolving user profiles), a
retrieval step over that state, an async review workflow, and a feedback
loop that changes future behavior based on past outcomes — properties that
only exist because the system runs and accumulates data over time.

---

## Providers / Stack

| Layer | Tool |
|---|---|
| LLM (reasoning, re-ranking, structured output) | **OpenAI API** (`gpt-4o-mini`, Responses API with `text_format` for schema-enforced structured output) |
| Embeddings (ad catalog + user profile vectors) | **OpenAI** (`text-embedding-3-small` — same model for indexing and queries, no re-indexing needed) |
| Vector store | **Pinecone** (serverless) — embeddings only, never the source of truth for status/budget |
| Relational store | **Postgres** via **SQLAlchemy** + **Alembic** migrations — advertisers, campaigns, budget, review outcome |
| Async job queue | **Redis** + **RQ** — campaign policy review runs off the request path |
| Tool integration | **MCP** (`langchain-mcp-adapters`) — the ad-policy document is served as an MCP resource the review agent fetches; the LLM call itself uses the OpenAI SDK directly (see docs/future_ideas.md for where an actual LangChain agent loop would fit in this project) |
| API layer | **FastAPI** |
| Reliability | `tenacity` (retries/backoff, `reraise=True` so callers see the real exception), Pydantic (structured output validation), atomic SQL updates for budget (no read-modify-write) |
| Observability | Structured (JSON) logging of every recommendation and review decision |
| Testing | `pytest` — 68 tests: mockable unit tests for LLM/vector-store boundaries, real-Postgres tests for anything DB-backed |
| Deployment | Docker Compose (postgres, redis, backend, worker, frontend) → Railway or Render for production |

---

## Data

- **Ad inventory**: not a static catalog — campaigns are submitted through
  the API (`POST /campaigns`) by advertisers, reviewed, and (if approved)
  embedded and indexed. There's no bulk-ingestion script; this is
  deliberate, since a static catalog with no owning campaign/budget/review
  record would bypass the whole review and budget system.
- **User profiles**: no accounts/login (see "Deliberate scope decisions"
  below) — a user is just a caller-supplied `user_id` string. `POST /users`
  seeds the starting profile vector from a free-text interest summary,
  stored (alongside a per-user blocklist) in Pinecone's `users` namespace —
  there's no Postgres table for this, the vector store is the only home for
  profile state. Synthetic personas (`data/generate_personas.py`) generate
  demo interest summaries; they're clearly labeled as synthetic.
- **Feedback**: like/dislike/interested reactions to a served ad update that
  user's profile vector and debit the serving campaign's budget; every
  impression/reaction/report is also logged to a Postgres `events` table
  (the history the performance dashboard aggregates over — Pinecone only
  ever holds current state, not a timeline).

---

## Architecture: two pipelines

### A. Campaigns — an advertiser submits, gets reviewed

1. **Submission** (`POST /campaigns`) — writes a row to Postgres with
   `status=pending_review` and returns immediately; the review itself runs
   asynchronously via a Redis/RQ job, not on the request path.
2. **Policy review** (`campaigns/review_jobs.py` → `campaigns/policy_review.py`)
   — an RQ worker picks up the job. The review agent fetches the current
   company ad-policy document via an **MCP resource** (`mcp_servers/
   ad_policy_server.py`, spawned as a short-lived stdio subprocess — the only
   client is this agent, not a human, so MCP resources rather than
   human-facing tools is the right fit here; this is the one piece of the
   review agent that uses LangChain, via `langchain-mcp-adapters` — see
   "Providers / Stack" below for why the LLM call itself doesn't). It then
   asks the LLM directly (OpenAI's Responses API, `text_format=
   ReviewDecision`, with the hosted `web_search` tool available for claims
   needing substantiation) for a validated three-way decision: `approved` /
   `rejected` / `needs_review`, with a reason, optional web-search-backed
   `research_notes` for a human moderator, and (if the campaign's category
   requires context exclusions per policy, e.g. alcohol) the exclusions to
   apply.
3. **Outcome**:
   - `approved` → creative gets embedded (`text-embedding-3-small`) and
     upserted into Pinecone (`campaigns/indexing.py`); campaign status →
     `active`.
   - `rejected` → reason stored on the row, visible to whoever submitted it.
   - `needs_review` → surfaces in the moderator queue (`GET /campaigns?
     status=needs_review`).
4. **Moderation** (`POST /campaigns/{id}/moderate`) — a human resolves a
   `needs_review` campaign. No authentication (out of scope for this
   project — see the deliberate no-login decision below); only
   *attribution*: the moderator's name is recorded as `reviewed_by` for the
   audit trail, without verifying who they actually are. Approval here
   triggers the same embed-and-index step as the automated path.

### B. Serving — recommending an ad to a user

0. **Profile creation** (`POST /users`, `serving/users.py`) — no accounts:
   a user is just a caller-supplied `user_id` string. This embeds a
   free-text interest summary and stores it (vector + summary + an empty
   blocklist) as a new record in Pinecone's `users` namespace. Every other
   step below requires this to have already run — there's no cold-start
   fallback that embeds text on the fly.
1. **Retrieval** (`serving/retrieval.py`) — read the user's *stored* profile
   vector back out of Pinecone (never re-embeds anything at recommend time),
   query the `ads` namespace for the nearest matches (cosine similarity, no
   LLM call), oversampling 3x top_k. The user's do-not-show blocklist and
   any campaign with an impression logged for them in the last hour
   (suppressing an ad from reappearing during a scroll session -- time-boxed
   rather than permanent, since a finite catalog would otherwise run out of
   things to show) are excluded via a Pinecone query-time `$nin` filter on
   the `campaign_id` metadata field, not a Python post-filter -- Pinecone's
   serverless index merges the vector and metadata indexes into one, so it
   searches past excluded IDs for real matches during the search itself,
   rather than risking an already-fetched, fixed-size batch coming up short.
   The surviving matches are then filtered against **Postgres** (the source
   of truth, not Pinecone metadata): only campaigns that are `status=active`,
   have `budget_spent < budget_total`, and are within their `start_date`/
   `end_date` window are kept, down to top_k -- this check stays a
   post-filter, since it can't move into Pinecone without duplicating
   Postgres as a second source of truth for status/budget.
2. **LLM re-ranking** (`serving/ranking.py`) — the surviving candidates +
   user context go to the LLM, which reasons about *intent*, not just
   vector similarity (e.g. someone reading about a leaky faucet wants a
   plumber ad *now*, not a general hardware store ad). Structured output
   (`{ad_id, relevance_score, justification}` per candidate) is enforced
   by OpenAI's Responses API schema (`text_format=RankingResponse`), not
   parsed-and-retried after the fact. `POST /recommend/batch` runs this
   once per page (one re-rank call covering up to `batch_size` candidates)
   rather than once per feed item, which is what actually makes a scrolling
   feed affordable.
3. **Guardrails** (`serving/guardrails.py`) — a rule-based check that blocks
   a specific ad from serving in the *current* context (e.g. no alcohol ads
   next to `sensitive`/`health`/`recovery` content). This is a different
   check from campaign policy review: guardrails run on every serve
   request against the live context; policy review runs once, at campaign
   creation, against the campaign's category in the abstract.
4. **Serve** — `POST /recommend` returns a single highest-ranked,
   guardrail-allowed candidate plus the full decision trace (candidates,
   rankings, guardrail results, what was actually served); `POST
   /recommend/batch` returns up to `batch_size` ranked, guardrail-allowed
   ads in one call, for the feed. Impressions are logged separately (next
   step) once an item actually scrolls into view — a batch being fetched
   isn't the same as every item in it having been seen.
5. **Events** (`serving/events_api.py`) — every impression and reaction is
   logged to a Postgres `events` table (the real history; Pinecone only
   ever holds current state, not a timeline):
   - `POST /events/impression` — pure DB insert, no profile nudge, no cost.
   - `POST /events/reaction` — `like`/`dislike`/`interested`. Logs the event
     and nudges the user's profile vector toward/away from the ad
     (re-normalized to unit length each time, `serving/feedback.py`);
     `like`/`interested` also debit the serving campaign's budget via an
     atomic SQL `UPDATE` (`budget_spent = budget_spent + cost`, not a
     Python read-modify-write, so concurrent reactions can't lose each
     other's contribution — see `feedback.py`'s `_debit_campaign_budget`).
     A campaign whose budget is exhausted auto-transitions to
     `status=completed`. There's no explicit "no reaction" event — silence
     is simply the absence of a row, not a signal.
   - `POST /events/report` — logs the event with a category (and reason, if
     `category=other`), then counts total reports for that campaign
     straight from the `events` table; crossing a flat threshold (default
     3) auto-flips the campaign to `needs_review`. Deliberately simple for
     now — see `docs/future_ideas.md` for the escalation-agent version this
     is expected to motivate.
   - `POST /users/{user_id}/do-not-show` — a permanent per-user exclusion,
     not a learning signal: no profile nudge, no event logged, just an
     addition to that user's blocklist (checked in step 1).
6. **Performance dashboard** (`GET /performance`, `serving/performance_api.py`)
   — aggregates the `events` table (plus `Campaign.budget_spent`, already
   the source of truth for spend) into overall CTR/engagement-rate/
   dislike-rate/spend/avg-CPA, a daily CTR trend line, and a per-campaign
   breakdown table. Aggregate across all activity, not scoped to a
   `user_id` — this is a window into the engine, not one person's feed.

### Explainability / logging

Every recommendation and every review decision emits structured JSON logs
(`core/logging_utils.py`) — candidate list, scores, LLM justification,
guardrail/policy decision, and the final outcome. This is the
decision-trace artifact: enough to answer "why was this ad chosen" or "why
was this campaign rejected" after the fact.

---

## Deliberate scope decisions

- **No bidding/auction** — serving ranking is pure relevance (the LLM's
  job), not `bid × relevance`. Campaigns just need `active` status and
  remaining budget to be eligible.
- **No authentication anywhere** — neither advertisers submitting campaigns
  nor moderators resolving them have accounts/logins. Moderation keeps
  *attribution* (a freeform name on the record) without verifying identity.
  Consciously out of scope, not an oversight.
- **MCP used for the agent-to-resource case, not human-facing** — an
  earlier design considered exposing the moderator queue itself as an MCP
  server a human would connect to via a client like Claude Desktop; this
  was rejected because MCP has no built-in auth/identity model, and a human
  taking a real approve/reject action needs one. The moderator queue is
  instead a normal (unauthenticated, per above) API; MCP is used only where
  it's a clean fit — the policy review agent fetching a reference document.

---

## Repo structure

`app/` is split into two pipelines plus shared modules — `core/` (infra),
`serving/`, and `campaigns/`:

```
backend/
├── app/
│   ├── main.py                    # FastAPI entrypoint, wires all routers
│   ├── models.py                   # SQLAlchemy: Advertiser, Campaign, Event (shared)
│   ├── schemas.py                   # Pydantic schemas (shared, see its module docstring)
│   ├── policy/
│   │   └── ad_policy.md              # company ad policy, served via MCP
│   ├── core/                         # shared infrastructure
│   │   ├── config.py                  # env-backed Settings
│   │   ├── db.py                      # SQLAlchemy engine/session
│   │   ├── queue.py                   # Redis connection + RQ queue
│   │   ├── logging_utils.py           # structured JSON logging
│   │   ├── embeddings.py              # OpenAI embeddings client
│   │   └── vector_store.py            # generic Pinecone client + fetch/upsert/update
│   ├── serving/                      # recommend an ad to a user
│   │   ├── api.py                     # POST /recommend, /recommend/batch
│   │   ├── users.py                   # POST/GET /users, do-not-show (blocklist)
│   │   ├── events_api.py              # impression/reaction/report endpoints
│   │   ├── performance_api.py          # GET /performance dashboard aggregation
│   │   ├── retrieval.py               # Pinecone query + eligibility + blocklist filter
│   │   ├── ranking.py                 # LLM re-ranking (OpenAI Responses API)
│   │   ├── guardrails.py              # brand-safety filtering (serve-time context)
│   │   └── feedback.py                # profile-vector update + budget debit
│   └── campaigns/                    # advertiser posts a campaign
│       ├── api.py                     # POST/GET /campaigns, /moderate
│       ├── policy_review.py           # LangChain + MCP policy review agent
│       ├── review_jobs.py             # RQ job: run review, persist outcome
│       └── indexing.py                # embed + upsert an approved campaign
├── mcp_servers/
│   └── ad_policy_server.py         # MCP resource server for the ad policy doc
├── alembic/                        # DB migrations
├── data/
│   ├── generate_personas.py        # synthetic user generation
│   └── seed_campaigns.json         # versioned seed campaign catalog (~288 campaigns)
├── tests/                          # 68 tests — see README for how to run
├── scripts/
│   ├── simulate_feedback_rounds.py       # runs multi-round CTR demo
│   ├── generate_seed_campaign_data.py    # LLM-generates data/seed_campaigns.json
│   └── seed_demo_campaigns.py            # loads that file into Postgres/Pinecone
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Demo artifacts

- `scripts/simulate_feedback_rounds.py` — runs N rounds of recommend →
  simulate a reaction → update profile (`record_feedback`) → re-recommend,
  and prints a rolling "like rate" improving across rounds, plus one example
  decision trace. The reaction is a single-turn tool-calling LLM call (raw
  OpenAI SDK, not LangChain -- no multi-step execution loop is needed): the
  model gets four tools (`like`/`dislike`/`interested`/`no_reaction`) and
  picks one based only on the persona's stated interest and the served ad's
  own content -- never the ranking algorithm's score or rank, which would
  make the whole demo circular (rewarding whatever the algorithm already put
  first instead of judging genuine fit). Also logs real impression/reaction
  events, so a run shows up in `GET /performance` like any other traffic.
- `scripts/generate_seed_campaign_data.py` + `scripts/seed_demo_campaigns.py`
  — populate a ~288-campaign catalog across 18 categories so the feed and
  onboarding checkpoints have real candidates. Split into a generation step
  (one LLM call per category, writes the versioned `data/seed_campaigns.json`)
  and a loading step (creates Advertiser/Campaign rows, sets status=active
  directly, embeds + indexes into Pinecone) -- the async policy-review job
  is skipped entirely, with category exclusions (alcohol/gambling) applied
  directly from `guardrails.py`'s `CATEGORY_EXCLUSIONS`. Re-running the
  loading step alone is free and deterministic; only re-run the generation
  step for a fresh/different catalog.
- The campaign review flow itself is a demo artifact by inspection: submit
  a campaign, watch it get reviewed asynchronously, see the policy agent's
  reasoning in the `review_reason` field and structured logs. There's no
  moderator-facing frontend page yet (see README status) — the moderator
  queue is exercised via the API directly.
