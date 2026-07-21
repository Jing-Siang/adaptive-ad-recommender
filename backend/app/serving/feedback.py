import numpy as np

from app.serving.retrieval import upsert_user_vector
from app.schemas import FeedbackEvent

# How strongly a single outcome nudges the profile vector toward/away from an ad.
LEARNING_RATE = {"click": 0.15, "conversion": 0.30, "no_click": -0.05}


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


def apply_feedback(event: FeedbackEvent, profile_vector: list[float], ad_vector: list[float]) -> list[float]:
    new_vector = update_profile_vector(profile_vector, ad_vector, event.outcome)
    upsert_user_vector(event.user_id, new_vector, metadata={"last_outcome": event.outcome, "last_ad_id": event.ad_id})
    return new_vector
