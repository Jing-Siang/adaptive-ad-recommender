import numpy as np
from sqlalchemy import update
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


def update_profile_vector(
    profile_vector: list[float],
    ad_vector: list[float],
    outcome: str,
) -> list[float]:
    """Nudge the user's profile embedding toward liked/interested ads and away from
    disliked ones, then re-normalize so distances stay comparable across rounds."""
    profile = np.array(profile_vector, dtype=float)
    ad = np.array(ad_vector, dtype=float)
    rate = LEARNING_RATE.get(outcome, 0.0)

    updated = profile + rate * (ad - profile)
    norm = np.linalg.norm(updated)
    if norm > 0:
        updated = updated / norm
    return updated.tolist()


def _debit_campaign_budget(db: Session, campaign_id: int, outcome: str) -> None:
    cost = COST_PER_OUTCOME.get(outcome, 0.0)
    if cost <= 0:
        return

    # Atomic increment at the SQL level (not a Python read-modify-write) so
    # concurrent feedback events on the same campaign can't lose each other's contribution.
    db.execute(update(Campaign).where(Campaign.id == campaign_id).values(budget_spent=Campaign.budget_spent + cost))
    db.commit()

    campaign = db.get(Campaign, campaign_id)
    if campaign and campaign.status == "active" and campaign.budget_spent >= campaign.budget_total:
        campaign.status = "completed"
        db.commit()


def record_feedback(db: Session, user_id: str, ad_id: str, outcome: str) -> list[float]:
    """Handle a reaction (like/dislike/interested) to a served ad end to end:
    nudge the user's profile vector toward/away from the ad, and debit the
    campaign's budget. Event-log insertion is the caller's job (see
    serving/events_api.py) -- this function only owns the profile/budget side.

    Requires a profile to already exist -- a reaction only ever fires on an ad
    that was actually served, which itself requires a profile (see
    retrieve_candidates), so a missing one here means the same kind of bug,
    not a legitimate case to paper over."""
    ad_vector = fetch_vector(ad_id, namespace="ads")
    if ad_vector is None:
        raise ValueError(f"ad '{ad_id}' not found")

    profile_vector = fetch_vector(user_id, namespace="users")
    if profile_vector is None:
        raise ValueError(f"no profile found for user '{user_id}' -- onboarding must run first")

    new_vector = update_profile_vector(profile_vector, ad_vector, outcome)
    update_vector(user_id, new_vector, namespace="users")

    _debit_campaign_budget(db, int(ad_id), outcome)

    return new_vector
