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
    """Feed-facing recommend: one embed, one Pinecone query, one guardrail
    pass -- returns up to batch_size guardrail-allowed ads, in the order
    retrieve_candidates already sorted them (vector similarity to the
    user's profile). The frontend displays these one at a time while
    scrolling and prefetches the next batch once the current one runs low.

    LLM re-ranking (the commented-out block below) is disabled for now.
    Measured against a real batch: a 50-candidate rerank call took 10.2s
    and only returned rankings for 10 of the 50 -- the model doesn't
    reliably score every item in a large structured-output batch, which is
    exactly why feed batches were sometimes coming back with only 10-20
    items instead of the requested ~50. Shrinking the batch to 15 fixed
    coverage (14/15 ranked) but not the latency -- still 9.6s, since the
    call's cost is dominated by fixed per-request overhead, not candidate
    count. ~13.5-14.2s total either way wasn't judged worth it for the
    "reasons about intent" upside over plain similarity order. To
    re-enable: uncomment the block and swap the fallback relevance_score/
    justification below for the real ranking's values."""
    user_id = str(current.id)
    with log_duration("recommend_batch", user_id=user_id):
        try:
            candidates = retrieve_candidates(db, user_id, top_k=request.batch_size)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not candidates:
            raise HTTPException(status_code=404, detail="no candidates found for user")

        # metadata = fetch_metadata(user_id, namespace="users") or {}
        # interest_summary = metadata.get("interest_summary", "")
        # rankings = rerank(user_context=interest_summary, candidates=candidates)
        # ranked_by_score = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)
        # candidates_by_id = {c.ad_id: c for c in candidates}

        items = []
        for candidate in candidates:
            guardrail = check_guardrails(
                Ad(**candidate.model_dump(exclude={"similarity_score"})),
                context_categories=set(),
            )
            if not guardrail.allowed:
                continue
            items.append(
                FeedItem(
                    **candidate.model_dump(),
                    # Clamped -- FeedItem.relevance_score is constrained to
                    # [0, 1] (a real LLM score always is), but raw cosine
                    # similarity isn't guaranteed to be, even though
                    # real-text embedding pairs land there in practice.
                    relevance_score=max(0.0, min(1.0, candidate.similarity_score)),
                    justification="Ranked by vector similarity to your profile.",
                )
            )

        log_event(
            "batch_recommendation_decision",
            user_id=user_id,
            candidate_count=len(candidates),
            served_count=len(items),
        )

        return BatchRecommendationResponse(user_id=user_id, items=items)
