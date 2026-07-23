from datetime import date

from sqlalchemy.orm import Session

from app.core.vector_store import fetch_vector, get_index
from app.models import Campaign
from app.schemas import AdCandidate

# How many extra Pinecone matches to pull beyond top_k, since some will be
# filtered out by campaign eligibility (budget/dates/status) after the fact.
_OVERSAMPLE_FACTOR = 3


def _eligible_campaign_ids(db: Session, candidate_ids: list[int]) -> set[int]:
    """Campaigns are only eligible to serve if still active, within budget, and
    within their date window -- checked against Postgres (the source of truth),
    not Pinecone metadata, which is never kept authoritative for this."""
    if not candidate_ids:
        return set()
    today = date.today()
    rows = (
        db.query(Campaign.id)
        .filter(
            Campaign.id.in_(candidate_ids),
            Campaign.status == "active",
            Campaign.budget_spent < Campaign.budget_total,
            Campaign.start_date <= today,
            Campaign.end_date >= today,
        )
        .all()
    )
    return {row.id for row in rows}


def retrieve_candidates(db: Session, user_id: str, top_k: int = 10) -> list[AdCandidate]:
    """Cheap first-pass retrieval: use the user's stored profile vector, query
    the `ads` namespace for the nearest ads by cosine similarity, then keep
    only campaigns still eligible to serve (active, budgeted, in-date). No
    LLM call.

    Requires a profile to already exist -- onboarding (POST /users, then the
    checkpoint flow) always creates one before a user ever reaches /recommend,
    so a missing profile here means recommend was called before onboarding
    finished, not a legitimate cold-start case to paper over."""
    vector = fetch_vector(user_id, namespace="users")
    if vector is None:
        raise ValueError(f"no profile found for user '{user_id}' -- onboarding must run first")
    index = get_index()
    result = index.query(
        vector=vector,
        top_k=top_k * _OVERSAMPLE_FACTOR,
        namespace="ads",
        include_metadata=True,
    )

    matches = result["matches"]
    candidate_ids = [int(match["id"]) for match in matches]
    eligible_ids = _eligible_campaign_ids(db, candidate_ids)

    candidates = []
    for match in matches:
        if int(match["id"]) not in eligible_ids:
            continue
        candidates.append(
            AdCandidate(
                ad_id=match["id"],
                headline=match["metadata"]["headline"],
                description=match["metadata"]["description"],
                category=match["metadata"]["category"],
                price=match["metadata"].get("price"),
                similarity_score=match["score"],
            )
        )
        if len(candidates) >= top_k:
            break
    return candidates
