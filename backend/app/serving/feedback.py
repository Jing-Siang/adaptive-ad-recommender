import numpy as np
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.vector_store import fetch_vector, update_vector
from app.models import Campaign

# How strongly a single reaction nudges the profile vector toward/away from an
# ad. dislike is a deliberate rejection -- a stronger negative signal than the
# old implicit "no_click" ever was, so it moves further than it did before.
LEARNING_RATE = {"like": 0.15, "interested": 0.30, "dislike": -0.20}

# Flat cost debited from a campaign's budget per reaction -- simple CPC/CPA-style
# pricing for the demo, not a real bidding auction (see docs/spec.md). Dislikes
# and impressions cost nothing; only like/interested are billable engagement.
COST_PER_OUTCOME = {"like": 0.50, "interested": 2.00, "dislike": 0.0}

# Atomic upsert of the user's current reaction to this ad: INSERT, or UPDATE
# in place on conflict, capturing the *old* reaction (via the `old` CTE)
# before it's overwritten -- all in one round-trip, so two near-simultaneous
# requests for the same (user_id, campaign_id) can't both see "no row yet"
# and each apply a full nudge/cost (see docs -- this is the fix for the
# reaction-idempotency gap).
_UPSERT_REACTION_SQL = text(
    """
    WITH old AS (
        SELECT reaction FROM reactions
        WHERE user_id = :user_id AND campaign_id = :campaign_id
    )
    INSERT INTO reactions (user_id, campaign_id, reaction, updated_at)
    VALUES (:user_id, :campaign_id, :new_reaction, now())
    ON CONFLICT (user_id, campaign_id)
    DO UPDATE SET reaction = EXCLUDED.reaction, updated_at = now()
    RETURNING reaction, (SELECT reaction FROM old) AS old_reaction
    """
)

# Session-level (not transaction-scoped) advisory lock keyed on user_id --
# guards the profile-vector fetch+write below, which Pinecone has no atomic
# primitive for (unlike the reaction upsert above). Session-level so the
# reaction-upsert's own db.commit() in between doesn't release it early;
# always released in record_feedback's `finally`, not tied to commit/rollback.
_ADVISORY_LOCK_SQL = text("SELECT pg_advisory_lock(hashtext(:user_id))")
_ADVISORY_UNLOCK_SQL = text("SELECT pg_advisory_unlock(hashtext(:user_id))")


def update_profile_vector(
    profile_vector: list[float],
    ad_vector: list[float],
    rate: float,
) -> list[float]:
    """Nudge the user's profile embedding toward/away from an ad by `rate`,
    then re-normalize so distances stay comparable across rounds. `rate` is
    the net rate to apply -- callers with an old/new outcome pair pass
    LEARNING_RATE[new] - LEARNING_RATE[old_or_none], not two stacked calls."""
    profile = np.array(profile_vector, dtype=float)
    ad = np.array(ad_vector, dtype=float)

    updated = profile + rate * (ad - profile)
    norm = np.linalg.norm(updated)
    if norm > 0:
        updated = updated / norm
    return updated.tolist()


def _debit_campaign_budget(db: Session, campaign_id: int, cost_delta: float) -> None:
    """Apply a (possibly negative, i.e. a refund) change to a campaign's
    budget_spent, and keep status in sync with the new total in either
    direction. `cost_delta` is the net change for a reaction switch, not a
    single outcome's flat cost -- see record_feedback."""
    if cost_delta == 0:
        return

    # Atomic increment at the SQL level (not a Python read-modify-write) so
    # concurrent feedback events on the same campaign can't lose each other's contribution.
    db.execute(
        update(Campaign).where(Campaign.id == campaign_id).values(budget_spent=Campaign.budget_spent + cost_delta)
    )
    db.commit()

    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return
    if campaign.status == "active" and campaign.budget_spent >= campaign.budget_total:
        campaign.status = "completed"
        db.commit()
    elif campaign.status == "completed" and campaign.budget_spent < campaign.budget_total:
        # A refund (switching to a cheaper/free reaction) can drop spend back
        # under budget -- safe to revive because the Kafka CDC consumer
        # (pinecone_sync_consumer.py) automatically re-syncs any status
        # change, so a revived campaign becomes servable again with no
        # manual re-indexing step.
        campaign.status = "active"
        db.commit()


def record_feedback(db: Session, user_id: str, ad_id: str, outcome: str) -> list[float]:
    """Handle a reaction (like/dislike/interested) to a served ad end to end:
    nudge the user's profile vector toward/away from the ad, and debit the
    campaign's budget. Event-log insertion is the caller's job (see
    serving/events_api.py) -- this function only owns the profile/budget side.

    Represents the user's *current* reaction, not a stack of every reaction
    ever made: re-clicking the same outcome is a true no-op, and switching
    outcomes applies the net delta rather than a second full nudge/debit on
    top of the first (see the `reactions` table in app/models.py).

    Requires a profile to already exist -- a reaction only ever fires on an ad
    that was actually served, which itself requires a profile (see
    retrieve_candidates), so a missing one here means the same kind of bug,
    not a legitimate case to paper over.

    Holds an advisory lock on user_id from before the profile-vector fetch
    through its write: without it, two genuinely concurrent reactions from
    the same user (e.g. reacting to two different ads back to back) could
    both fetch the same starting vector and the second write would silently
    clobber the first's nudge (see docs/future_ideas.md)."""
    campaign_id = int(ad_id)

    ad_vector = fetch_vector(ad_id, namespace="ads")
    if ad_vector is None:
        raise ValueError(f"ad '{ad_id}' not found")

    db.execute(_ADVISORY_LOCK_SQL, {"user_id": user_id})
    try:
        profile_vector = fetch_vector(user_id, namespace="users")
        if profile_vector is None:
            raise ValueError(f"no profile found for user '{user_id}' -- onboarding must run first")

        row = db.execute(
            _UPSERT_REACTION_SQL,
            {"user_id": user_id, "campaign_id": campaign_id, "new_reaction": outcome},
        ).one()
        db.commit()
        old_outcome = row.old_reaction

        if old_outcome == outcome:
            return profile_vector

        rate_delta = LEARNING_RATE.get(outcome, 0.0) - LEARNING_RATE.get(old_outcome, 0.0)
        new_vector = update_profile_vector(profile_vector, ad_vector, rate_delta)
        update_vector(user_id, new_vector, namespace="users")
    finally:
        db.execute(_ADVISORY_UNLOCK_SQL, {"user_id": user_id})

    cost_delta = COST_PER_OUTCOME.get(outcome, 0.0) - COST_PER_OUTCOME.get(old_outcome, 0.0)
    _debit_campaign_budget(db, campaign_id, cost_delta)

    return new_vector
