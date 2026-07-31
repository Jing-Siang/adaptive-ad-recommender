from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging_utils import log_event
from app.core.vector_store import fetch_metadata, fetch_vector, get_index
from app.models import Campaign, Event
from app.schemas import AdCandidate

# How many extra Pinecone matches to pull beyond top_k. Pinecone's own query
# now filters on status="active" and the start_date/end_date window (kept in
# sync by pinecone_sync_consumer.py, see docs/kafka_cdc_plan.md), so most
# ineligible ads are already excluded before this point -- this stays as a
# safety net for what that can't cover: CDC propagation lag (status/budget
# changes take a little time to reach Pinecone after the Postgres commit),
# and campaigns whose Pinecone metadata hasn't been backfilled with dates
# yet. Left unchanged rather than tuned down without real observed data on
# how much less oversampling is actually needed now.
_OVERSAMPLE_FACTOR = 3

# Debezium/Postgres logical-replication encodes `date` columns as days since
# the Unix epoch (io.debezium.time.Date) -- pinecone_sync_consumer.py passes
# start_date/end_date straight through in that form, so "today" needs to be
# expressed the same way to compare against them in Pinecone's filter.
_EPOCH = date(1970, 1, 1)

# How long a served ad is suppressed from reappearing to the same user after
# an impression, to avoid the "same ad again immediately" feel during a
# scroll session. Time-boxed rather than permanent, since the demo catalog
# is finite -- a permanent exclusion would eventually starve the feed.
_ALREADY_SHOWN_WINDOW = timedelta(hours=1)


def _eligible_campaign_ids(db: Session, candidate_ids: list[int]) -> set[int]:
    """Campaigns are only eligible to serve if still active, within budget, and
    within their date window -- checked against Postgres (the source of truth)
    regardless of what Pinecone's own status/date filter already did at query
    time. Status/budget/dates are now also pushed into Pinecone (kept in sync
    by pinecone_sync_consumer.py), which is what lets the oversample above stay
    small in practice -- but this check stays as the final authority, since
    Pinecone is a synced copy with real (if usually small) propagation lag,
    never the source of truth itself."""
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
    # status/dates are kept in sync by pinecone_sync_consumer.py -- filtering
    # on them here is what actually makes the oversample above mostly
    # unnecessary, not just documentation of intent. Still not a substitute
    # for the Postgres check below (CDC propagation lag; campaigns not yet
    # backfilled with date metadata).
    today_epoch_days = (date.today() - _EPOCH).days
    filter_ = {
        "status": {"$eq": "active"},
        "start_date": {"$lte": today_epoch_days},
        "end_date": {"$gte": today_epoch_days},
    }
    if excluded_ids:
        filter_["campaign_id"] = {"$nin": sorted(excluded_ids)}
    query_kwargs["filter"] = filter_
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

    # Observability for _OVERSAMPLE_FACTOR tuning: trimmed = how many of
    # Pinecone's own (already status/date-filtered) matches still got cut by
    # the Postgres safety net -- almost entirely CDC propagation lag at this
    # point. Log real data before ever changing the factor's value again
    # (see docs/kafka_cdc_plan.md Phase 4) rather than guessing.
    log_event(
        "retrieve_candidates_oversample",
        top_k=top_k,
        pinecone_matches=len(matches),
        trimmed_by_postgres_check=len(candidate_ids) - len(eligible_ids),
        returned=len(candidates),
        short=len(candidates) < top_k,
    )
    return candidates
