from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.campaigns.api import router as campaigns_router
from app.core.config import settings
from app.serving.guardrails import check_guardrails
from app.core.logging_utils import log_duration, log_event
from app.serving.ranking import rerank
from app.serving.retrieval import retrieve_candidates
from app.schemas import (
    Ad,
    FeedbackEvent,
    RecommendationRequest,
    RecommendationTrace,
)

app = FastAPI(title="Adaptive Ad Recommender", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaigns_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/recommend", response_model=RecommendationTrace)
def recommend(request: RecommendationRequest) -> RecommendationTrace:
    with log_duration("recommend", user_id=request.user_id):
        candidates = retrieve_candidates(request.user_id, top_k=request.top_k)
        if not candidates:
            raise HTTPException(status_code=404, detail="no candidates found for user")

        rankings = rerank(user_context=request.user_id, candidates=candidates)
        ranked_by_score = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)

        candidates_by_id = {c.ad_id: c for c in candidates}
        guardrail_results = [
            check_guardrails(
                Ad(**candidates_by_id[r.ad_id].model_dump(exclude={"similarity_score"})),
                context_categories=set(),
            )
            for r in ranked_by_score
            if r.ad_id in candidates_by_id
        ]
        allowed_ids = {g.ad_id for g in guardrail_results if g.allowed}
        served_ad_id = next((r.ad_id for r in ranked_by_score if r.ad_id in allowed_ids), None)

        log_event(
            "recommendation_decision",
            user_id=request.user_id,
            served_ad_id=served_ad_id,
            candidate_count=len(candidates),
        )

        return RecommendationTrace(
            user_id=request.user_id,
            candidates=candidates,
            rankings=rankings,
            guardrail_results=guardrail_results,
            served_ad_id=served_ad_id,
        )


@app.post("/feedback")
def feedback(event: FeedbackEvent) -> dict:
    log_event("feedback_received", **event.model_dump())
    return {"status": "recorded"}
