# Contextual Ad Recommendation Engine — Project Spec

## Overview

An end-to-end AI engineering system that recommends the most relevant ad to
a user based on their profile/context, learns from feedback over time, and
is built with production-grade reliability (not just a notebook demo).

**Goal:** combine RAG-style retrieval, LLM reasoning/re-ranking, agentic
tool use, and LLMOps practices into a single cohesive ad-recommendation
system.

**Why this project:** it can't be solved with a single LLM prompt. It
requires persistent state (a growing ad catalog and evolving user profiles),
a retrieval step over that state, and a feedback loop that changes future
behavior based on past outcomes — properties that only exist because the
system runs and accumulates data over time.

---

## Providers / Stack

| Layer | Tool |
|---|---|
| LLM (reasoning, re-ranking, structured output) | **OpenAI API** (`gpt-4o-mini`, Responses API with `text_format` for schema-enforced structured output) |
| Embeddings (ad catalog + user profile vectors) | **OpenAI** (`text-embedding-3-small` — same model for indexing and queries, no re-indexing needed) |
| Vector store | **Pinecone** |
| Orchestration / agent loop | **LangChain** (with `langchain-mcp-adapters` if MCP tools are used) |
| Tool delivery (optional) | **MCP server** for sending notifications (Slack/email) |
| API layer | **FastAPI** |
| Reliability | `tenacity` (retries/backoff), Pydantic (structured output validation) |
| Observability | Structured (JSON) logging of every recommendation decision |
| Testing | `pytest` — unit tests for scoring/tool functions, a small eval set of prompts |
| Deployment | Docker → Railway or Render |

---

## Data

- **Ad inventory**: use a public product dataset (e.g. the Amazon Products
  dataset on Kaggle) as a proxy for ad listings. Each item has a title,
  description, category, and price — treated as "ad copy."
- **User profiles**: synthetic personas with a generated browsing/interest
  history (LLM-generated for the demo, clearly labeled as synthetic in the
  README).
- **Feedback**: simulated clicks/conversions used to update each user's
  profile vector over multiple rounds.

---

## Architecture (pipeline)

### 1. Ad inventory embedding
- Ingest the product catalog.
- Embed each ad's text (title + description + category) with OpenAI
  (`text-embedding-3-small`).
- Store vectors + metadata in Pinecone (namespace: `ads`).

### 2. User profile embedding
- Generate synthetic user personas with an interest history (LLM-generated).
- Embed a "rolling profile" representing the user's current interests.
- Store/update in Pinecone (namespace: `users`) or keep in a lightweight DB
  and re-embed on update.

### 3. Retrieval (cheap first pass)
- Given a user, query Pinecone for the top-K ads whose embeddings are
  closest (cosine similarity) to the user's profile vector.
- This is a pure vector-search step — no LLM call, keeps cost/latency low.

### 4. LLM re-ranking
- Pass the top-K candidate ads + user context to the LLM.
- It reasons about *intent*, not just similarity (e.g., someone reading
  about a leaky faucet wants a plumber ad *now*, not a general hardware
  store ad).
- Require **structured output**: `{ad_id, relevance_score, justification}`
  per candidate, validated against a Pydantic schema before use.

### 5. Guardrails / brand safety
- A filtering step (rule-based and/or LLM-checked) that blocks
  inappropriate ad-category pairings (e.g., no alcohol ads next to
  sensitive content, respect category exclusions per advertiser).
- Runs after re-ranking, before an ad is served.

### 6. Feedback loop
- Simulate a click/no-click/conversion outcome for the served ad.
- Update the user's profile vector based on the outcome (e.g., nudge the
  profile embedding toward clicked ads, away from ignored ones).
- Track CTR across simulated rounds to demonstrate the system "learning"
  over time — this is the key demo artifact.

### 7. Delivery (optional, for the agentic/MCP angle)
- Instead of just returning JSON, use an MCP server (e.g. Slack or email)
  as a tool the agent can call to "deliver" a recommendation report or
  alert — ties the MCP coursework into the project.

### 8. Explainability / logging
- For every served ad, log: candidate list, similarity scores, Claude's
  justification, guardrail decision, and final choice.
- This creates a full decision trace — useful for debugging and as a demo
  artifact ("here's why this ad was chosen for this user").

---

## Production hardening (explicitly required, not optional)

- **Rate limits/retries**: wrap all Claude and Voyage API calls with
  `tenacity` — exponential backoff + jitter, respect `Retry-After` on 429s.
- **Structured output validation**: every LLM response that's used
  downstream must be validated against a Pydantic model before trusting it;
  reject/retry on validation failure.
- **Logging**: structured JSON logs per request — latency, token usage,
  which tools/calls were made, and the final decision.
- **Tests**: unit tests for the retrieval function, the re-ranking parser,
  and the guardrail filter (all mockable/deterministic); a small eval set
  of (user, ad-candidates) pairs with expected pass/fail guardrail behavior.
- **Deployment**: FastAPI app in Docker, deployed to Railway or Render, with
  secrets in environment variables (never hardcoded).

---

## Repo structure

`app/` is split into two pipelines plus shared modules — `core/` (infra),
`serving/` (retrieve → re-rank → guardrail → serve → feedback), and
`campaigns/` (advertiser submits → policy review → moderation):

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
│   │   └── embeddings.py              # OpenAI embeddings client
│   ├── serving/                      # recommend an ad to a user
│   │   ├── retrieval.py               # Pinecone query + indexing logic
│   │   ├── ranking.py                 # LLM re-ranking (OpenAI Responses API)
│   │   ├── guardrails.py              # brand-safety filtering
│   │   └── feedback.py                # profile-vector update logic
│   └── campaigns/                    # advertiser posts a campaign
│       ├── api.py                     # POST/GET /campaigns, /moderate
│       ├── policy_review.py           # LangChain + MCP policy review agent
│       └── review_jobs.py             # RQ job: run review, persist outcome
├── mcp_servers/
│   └── ad_policy_server.py         # MCP resource server for the ad policy doc
├── alembic/                        # DB migrations
├── data/
│   └── generate_personas.py        # synthetic user generation
├── tests/
├── scripts/
│   └── simulate_feedback_rounds.py   # runs multi-round CTR demo
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Demo artifact

A short script/notebook (`scripts/simulate_feedback_rounds.py`) that runs
N rounds of: recommend → simulate click → update profile → re-recommend,
and plots/prints CTR improving across rounds, plus one example decision
trace showing the full reasoning behind a single served ad.
