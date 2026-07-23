import numpy as np
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.vector_store import fetch_vector, update_vector
from app.models import Campaign
from app.schemas import FeedbackEvent

# How strongly a single outcome nudges the profile vector toward/away from an ad.
LEARNING_RATE = {"click": 0.15, "conversion": 0.30, "no_click": -0.05}

# Flat cost debited from a campaign's budget per outcome -- simple CPC/CPA-style
# pricing for the demo, not a real bidding auction (see docs/spec.md). Impressions
# with no click cost nothing.
COST_PER_OUTCOME = {"click": 0.50, "conversion": 2.00, "no_click": 0.0}


def update_profile_vector(
    profile_vector: list[float],
    ad_vector: list[float],
    outcome: str,
) -> list[float]:
    """Nudge the user's profile embedding toward clicked/converted ads and away from
    ignored ones, then re-normalize so distances stay comparable across rounds."""
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


def record_feedback(db: Session, event: FeedbackEvent) -> list[float]:
    """Handle a served ad's feedback outcome end to end: nudge the user's profile
    vector toward/away from the ad, and debit the campaign's budget.

    Requires a profile to already exist -- feedback only ever fires on an ad
    that was actually served, which itself requires a profile (see
    retrieve_candidates), so a missing one here means the same kind of bug,
    not a legitimate case to paper over."""
    ad_vector = fetch_vector(event.ad_id, namespace="ads")
    if ad_vector is None:
        raise ValueError(f"ad '{event.ad_id}' not found")

    profile_vector = fetch_vector(event.user_id, namespace="users")
    if profile_vector is None:
        raise ValueError(f"no profile found for user '{event.user_id}' -- onboarding must run first")

    new_vector = update_profile_vector(profile_vector, ad_vector, event.outcome)
    update_vector(event.user_id, new_vector, namespace="users")

    _debit_campaign_budget(db, int(event.ad_id), event.outcome)

    return new_vector
