from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.logging_utils import log_duration, log_event
from app.core.vector_store import fetch_metadata
from app.serving.guardrails import check_guardrails
from app.serving.ranking import rerank
from app.serving.retrieval import retrieve_candidates
from app.schemas import (
    Ad,
    BatchRecommendationRequest,
    BatchRecommendationResponse,
    CurrentUser,
    FeedItem,
)

router = APIRouter(tags=["serving"])


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
    batch once the current one runs low, instead of re-ranking once per
    scroll item."""
    user_id = str(current.id)
    with log_duration("recommend_batch", user_id=user_id):
        try:
            candidates = retrieve_candidates(db, user_id, top_k=request.batch_size)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not candidates:
            raise HTTPException(status_code=404, detail="no candidates found for user")

        # The profile's interest summary, not the bare user_id -- rerank's
        # system prompt asks the LLM to reason about the user's *intent*,
        # which it can't do with just an opaque numeric id as "context".
        metadata = fetch_metadata(user_id, namespace="users") or {}
        interest_summary = metadata.get("interest_summary", "")
        rankings = rerank(user_context=interest_summary, candidates=candidates)
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
