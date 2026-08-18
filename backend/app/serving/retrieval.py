from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging_utils import log_event
from app.core.vector_store import fetch_vector, get_index
from app.models import BlocklistEntry, Campaign, Event
from app.schemas import AdCandidate

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
    time. Status/budget/dates are kept in sync in Pinecone by
    pinecone_sync_consumer.py, but this check stays as the final authority,
    since Pinecone is a synced copy with real (if usually small) propagation
    lag, never the source of truth itself."""
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


def _blocklisted_campaign_ids(db: Session, user_id: str) -> set[int]:
    """Permanent per-user "do not show again" exclusions -- see
    serving/users.py's do_not_show. Lives in Postgres (blocklist_entries),
    not Pinecone metadata -- a plain indexed query here, not a Pinecone
    round trip."""
    rows = db.query(BlocklistEntry.campaign_id).filter(BlocklistEntry.user_id == int(user_id)).all()
    return {row.campaign_id for row in rows}


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

    excluded_ids = _blocklisted_campaign_ids(db, user_id) | _recently_shown_campaign_ids(db, user_id)

    index = get_index()
    query_kwargs = {
        "vector": vector,
        "top_k": top_k,
        "namespace": "ads",
        "include_metadata": True,
    }
    # status/dates are kept in sync by pinecone_sync_consumer.py, so this
    # query is already status/date-correct at the source almost all the
    # time -- no oversampling beyond top_k. A no-longer-eligible campaign
    # can still occasionally slip through during the brief CDC propagation
    # window (measured: negligible under realistic churn, see
    # docs/kafka_cdc_plan.md Phase 4); when that happens the Postgres check
    # below just returns fewer than top_k rather than over-fetching on
    # every call to guard against it -- an occasional shorter batch is fine
    # for a scrolling feed, not worth paying for on every request.
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

    candidates = [
        AdCandidate(
            ad_id=match["id"],
            headline=match["metadata"]["headline"],
            description=match["metadata"]["description"],
            category=match["metadata"]["category"],
            price=match["metadata"].get("price"),
            similarity_score=match["score"],
        )
        for match in matches
        if int(match["id"]) in eligible_ids
    ]

    # Watches how often the Postgres safety net actually has to trim a
    # Pinecone match (CDC propagation lag) -- expected to be rare. A short
    # batch here just means fewer ads this scroll, not an error; if `short`
    # starts showing up often in practice, that's the signal to revisit
    # this (see docs/kafka_cdc_plan.md Phase 4), not a fixed oversample.
    log_event(
        "retrieve_candidates_trim",
        top_k=top_k,
        pinecone_matches=len(matches),
        trimmed_by_postgres_check=len(candidate_ids) - len(eligible_ids),
        returned=len(candidates),
        short=len(candidates) < top_k,
    )
    return candidates
