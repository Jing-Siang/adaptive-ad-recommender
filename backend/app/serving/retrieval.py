from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.vector_store import fetch_metadata, fetch_vector, get_index
from app.models import Campaign, Event
from app.schemas import AdCandidate

# How many extra Pinecone matches to pull beyond top_k, since some will be
# filtered out by campaign eligibility (budget/dates/status) after the fact --
# that check can't move into Pinecone's own filter (see _eligible_campaign_ids).
_OVERSAMPLE_FACTOR = 3

# How long a served ad is suppressed from reappearing to the same user after
# an impression, to avoid the "same ad again immediately" feel during a
# scroll session. Time-boxed rather than permanent, since the demo catalog
# is finite -- a permanent exclusion would eventually starve the feed.
_ALREADY_SHOWN_WINDOW = timedelta(hours=1)


def _eligible_campaign_ids(db: Session, candidate_ids: list[int]) -> set[int]:
    """Campaigns are only eligible to serve if still active, within budget, and
    within their date window -- checked against Postgres (the source of truth),
    not Pinecone metadata, which is never kept authoritative for this. Applied
    as a post-filter (unlike blocklist/recently-shown below) because this data
    can't be pushed into Pinecone's query-time filter without duplicating
    Postgres as a second source of truth for status/budget."""
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


def _recently_shown_campaign_ids(db: Session, user_id: str) -> set[int]:
    """Campaigns with an impression logged for this user within
    _ALREADY_SHOWN_WINDOW. Passed to Pinecone as a query-time exclusion
    filter on the `campaign_id` metadata field (Pinecone doesn't support
    filtering by vector ID directly, only by metadata -- $nin on
    campaign_id, already stored per ad in campaigns/indexing.py). Pinecone's
    serverless index merges the vector and metadata indexes into one, so
    excluded IDs are skipped *during* the search rather than truncated from
    an already-fetched, fixed-size batch after the fact -- the more a user
    has scrolled recently, the more a naive post-filter could come up short."""
    cutoff = datetime.now(UTC) - _ALREADY_SHOWN_WINDOW
    rows = (
        db.query(Event.campaign_id)
        .filter(Event.user_id == user_id, Event.event_type == "impression", Event.created_at >= cutoff)
        .distinct()
        .all()
    )
    return {row.campaign_id for row in rows}


def retrieve_candidates(
    db: Session, user_id: str, top_k: int = 10, vector: list[float] | None = None
) -> list[AdCandidate]:
    """Cheap first-pass retrieval: use the user's stored profile vector, query
    the `ads` namespace for the nearest ads by cosine similarity (excluding
    blocklisted and recently-shown campaigns at query time), then keep only
    campaigns still eligible to serve (active, budgeted, in-date). No LLM call.

    Requires a profile to already exist -- onboarding (POST /users, then the
    checkpoint flow) always creates one before a user ever reaches /recommend,
    so a missing profile here means recommend was called before onboarding
    finished, not a legitimate cold-start case to paper over.

    vector is optional: pass it directly if the caller already has it (e.g.
    the onboarding checkpoint, right after seeding a brand-new profile) to
    skip re-fetching from Pinecone -- upserts aren't always immediately
    readable, so re-fetching a vector moments after writing it can spuriously
    raise the "no profile found" error below even though the write succeeded."""
    if vector is None:
        vector = fetch_vector(user_id, namespace="users")
        if vector is None:
            raise ValueError(f"no profile found for user '{user_id}' -- onboarding must run first")

    blocklist = {int(x) for x in (fetch_metadata(user_id, namespace="users") or {}).get("blocklist", [])}
    excluded_ids = blocklist | _recently_shown_campaign_ids(db, user_id)

    index = get_index()
    query_kwargs = {
        "vector": vector,
        "top_k": top_k * _OVERSAMPLE_FACTOR,
        "namespace": "ads",
        "include_metadata": True,
    }
    if excluded_ids:
        query_kwargs["filter"] = {"campaign_id": {"$nin": sorted(excluded_ids)}}
    result = index.query(**query_kwargs)

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
