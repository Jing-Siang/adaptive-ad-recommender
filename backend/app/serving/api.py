from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.logging_utils import log_duration, log_event
from app.serving.guardrails import check_guardrails
from app.serving.ranking import rerank
from app.serving.retrieval import retrieve_candidates
from app.schemas import (
    Ad,
    BatchRecommendationRequest,
    BatchRecommendationResponse,
    CurrentUser,
    FeedItem,
    RecommendationRequest,
    RecommendationTrace,
)

router = APIRouter(tags=["serving"])


@router.post("/recommend", response_model=RecommendationTrace)
def recommend(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> RecommendationTrace:
    """Single-item recommend with a full decision trace -- kept for the demo
    script and one-off explainability use cases. The feed uses
    /recommend/batch instead, to amortize the re-rank call across N items."""
    user_id = str(current.id)
    with log_duration("recommend", user_id=user_id):
        try:
            candidates = retrieve_candidates(db, user_id, top_k=request.top_k)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not candidates:
            raise HTTPException(status_code=404, detail="no candidates found for user")

        rankings = rerank(user_context=user_id, candidates=candidates)
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
            user_id=user_id,
            served_ad_id=served_ad_id,
            candidate_count=len(candidates),
        )

        return RecommendationTrace(
            user_id=user_id,
            candidates=candidates,
            rankings=rankings,
            guardrail_results=guardrail_results,
            served_ad_id=served_ad_id,
        )


@router.post("/recommend/batch", response_model=BatchRecommendationResponse)
def recommend_batch(
    request: BatchRecommendationRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> BatchRecommendationResponse:
    """Feed-facing recommend: one embed, one Pinecone query, one LLM re-rank
    call covering the whole batch, one guardrail pass -- returns up to
    batch_size ranked, guardrail-allowed ads in one call. The frontend
    displays these one at a time while scrolling and prefetches the next
    batch once the current one runs low, instead of calling /recommend once
    per scroll item (which would re-run the LLM re-rank on every item)."""
    user_id = str(current.id)
    with log_duration("recommend_batch", user_id=user_id):
        try:
            candidates = retrieve_candidates(db, user_id, top_k=request.batch_size)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not candidates:
            raise HTTPException(status_code=404, detail="no candidates found for user")

        rankings = rerank(user_context=user_id, candidates=candidates)
        ranked_by_score = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)
        candidates_by_id = {c.ad_id: c for c in candidates}

        items = []
        for ranking in ranked_by_score:
            candidate = candidates_by_id.get(ranking.ad_id)
            if candidate is None:
                continue
            guardrail = check_guardrails(
                Ad(**candidate.model_dump(exclude={"similarity_score"})),
                context_categories=set(),
            )
            if not guardrail.allowed:
                continue
            items.append(
                FeedItem(
                    **candidate.model_dump(),
                    relevance_score=ranking.relevance_score,
                    justification=ranking.justification,
                )
            )

        log_event(
            "batch_recommendation_decision",
            user_id=user_id,
            candidate_count=len(candidates),
            served_count=len(items),
        )

        return BatchRecommendationResponse(user_id=user_id, items=items)
