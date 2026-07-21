# Adaptive Ad Recommender

A contextual ad recommendation engine: recommends the most relevant ad to a
user based on their profile/context, learns from feedback over time, and is
built with production-grade reliability rather than a notebook demo.

It combines RAG-style retrieval, LLM re-ranking, guardrails, and a feedback
loop that measurably improves CTR across simulated rounds. See
[`docs/spec.md`](docs/spec.md) for the full design.

## Stack

| Layer | Tool |
|---|---|
| LLM (reasoning, re-ranking, structured output) | OpenAI API (`gpt-4o-mini`, Responses API) |
| Embeddings | OpenAI (`text-embedding-3-small`) |
| Vector store | Pinecone |
| Backend | FastAPI |
| Frontend | React + Vite + TypeScript |
| Reliability | `tenacity` (retries/backoff), Pydantic (structured output validation) |
| Testing | `pytest` |
| Deployment | Docker → Railway/Render (backend), static host (frontend) |

## Repo layout

```
adaptive-ad-recommender/
├── backend/         # FastAPI service: retrieval, re-ranking, guardrails, feedback loop
│   ├── app/
│   ├── data/         # catalog ingestion + synthetic persona generation
│   ├── tests/
│   └── scripts/      # simulate_feedback_rounds.py demo artifact
├── frontend/         # React + Vite + TS dashboard
│   └── src/
├── docs/
│   └── spec.md
└── docker-compose.yml
```

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY, PINECONE_API_KEY
uvicorn app.main:app --reload
```

Runs at `http://localhost:8000` (interactive docs at `/docs`).

Tests:

```bash
cd backend && source .venv/bin/activate && pytest
```

### Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Runs at `http://localhost:5173`.

### Both, via Docker Compose

```bash
docker compose up --build
```

## Status

Early scaffold — backend pipeline modules (embeddings, retrieval, ranking,
guardrails, feedback) and unit tests are in place; frontend is a placeholder
pending the recommendation dashboard, decision-trace viewer, and CTR chart.
