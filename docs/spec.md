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
| Agent framework | **LangChain** (`langchain-openai`) — used specifically for the policy review agent's structured output |
| Tool integration | **MCP** (`langchain-mcp-adapters`) — the ad-policy document is served as an MCP resource that the review agent fetches |
| API layer | **FastAPI** |
| Reliability | `tenacity` (retries/backoff, `reraise=True` so callers see the real exception), Pydantic (structured output validation), atomic SQL updates for budget (no read-modify-write) |
| Observability | Structured (JSON) logging of every recommendation and review decision |
| Testing | `pytest` — 42 tests: mockable unit tests for LLM/vector-store boundaries, real-Postgres tests for anything DB-backed |
| Deployment | Docker Compose (postgres, redis, backend, worker, frontend) → Railway or Render for production |

---

## Data

- **Ad inventory**: not a static catalog — campaigns are submitted through
  the API (`POST /campaigns`) by advertisers, reviewed, and (if approved)
  embedded and indexed. There's no bulk-ingestion script; this is
  deliberate, since a static catalog with no owning campaign/budget/review
  record would bypass the whole review and budget system.
- **User profiles**: synthetic personas with a generated browsing/interest
  history (LLM-generated for the demo, clearly labeled as synthetic — see
  `data/generate_personas.py`).
- **Feedback**: simulated click/no_click/conversion outcomes used to update
  each user's profile vector and debit the serving campaign's budget over
  multiple rounds.

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
   human-facing tools is the right fit here), then asks the LLM
   (`langchain-openai`'s `ChatOpenAI` + `.with_structured_output`) for a
   validated three-way decision: `approved` / `rejected` / `needs_review`,
   with a reason and (if the campaign's category requires context
   exclusions per policy, e.g. alcohol) the exclusions to apply.
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

1. **Retrieval** (`serving/retrieval.py`) — embed the user's profile text,
   query Pinecone's `ads` namespace for the nearest matches (cosine
   similarity, no LLM call), oversampling 3x top_k. Matches are then
   filtered against **Postgres** (the source of truth, not Pinecone
   metadata): only campaigns that are `status=active`, have
   `budget_spent < budget_total`, and are within their `start_date`/
   `end_date` window are kept, down to top_k.
2. **LLM re-ranking** (`serving/ranking.py`) — the surviving candidates +
   user context go to the LLM, which reasons about *intent*, not just
   vector similarity (e.g. someone reading about a leaky faucet wants a
   plumber ad *now*, not a general hardware store ad). Structured output
   (`{ad_id, relevance_score, justification}` per candidate) is enforced
   by OpenAI's Responses API schema (`text_format=RankingResponse`), not
   parsed-and-retried after the fact.
3. **Guardrails** (`serving/guardrails.py`) — a rule-based check that blocks
   a specific ad from serving in the *current* context (e.g. no alcohol ads
   next to `sensitive`/`health`/`recovery` content). This is a different
   check from campaign policy review: guardrails run on every serve
   request against the live context; policy review runs once, at campaign
   creation, against the campaign's category in the abstract.
4. **Serve** (`POST /recommend`) — the highest-ranked, guardrail-allowed
   candidate is served; the full decision trace (candidates, rankings,
   guardrail results, what was actually served) is returned and logged.
5. **Feedback** (`POST /feedback`, `serving/feedback.py`) — a click/
   no_click/conversion outcome for the served ad: nudges the user's profile
   vector toward/away from the ad (re-normalized to unit length each time),
   and debits the serving campaign's budget via an atomic SQL `UPDATE`
   (`budget_spent = budget_spent + cost`, not a Python read-modify-write,
   so concurrent feedback events can't lose each other's contribution — see
   `feedback.py`'s `_debit_campaign_budget`). A campaign whose budget is
   exhausted auto-transitions to `status=completed`, making it ineligible
   for the next retrieval pass.

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
│   ├── main.py                    # FastAPI entrypoint, wires both routers
│   ├── models.py                   # SQLAlchemy: Advertiser, Campaign (shared)
│   ├── schemas.py                   # Pydantic schemas (shared, see its module docstring)
│   ├── policy/
│   │   └── ad_policy.md              # company ad policy, served via MCP
│   ├── core/                         # shared infrastructure
│   │   ├── config.py                  # env-backed Settings
│   │   ├── db.py                      # SQLAlchemy engine/session
│   │   ├── queue.py                   # Redis connection + RQ queue
│   │   ├── logging_utils.py           # structured JSON logging
│   │   ├── embeddings.py              # OpenAI embeddings client
│   │   └── vector_store.py            # generic Pinecone client + fetch/upsert
│   ├── serving/                      # recommend an ad to a user
│   │   ├── retrieval.py               # Pinecone query + campaign eligibility filter
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
│   └── generate_personas.py        # synthetic user generation
├── tests/                          # 42 tests — see README for how to run
├── scripts/
│   └── simulate_feedback_rounds.py   # runs multi-round CTR demo
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Demo artifacts

- `scripts/simulate_feedback_rounds.py` — runs N rounds of recommend →
  simulate click → update profile (`record_feedback`) → re-recommend, and
  prints rolling CTR improving across rounds, plus one example decision
  trace.
- The campaign review flow itself is a demo artifact by inspection: submit
  a campaign, watch it get reviewed asynchronously, see the policy agent's
  reasoning in the `review_reason` field and structured logs. There's no
  moderator-facing frontend page yet (see README status) — the moderator
  queue is exercised via the API directly.
