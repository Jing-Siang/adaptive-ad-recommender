from unittest.mock import patch

import numpy as np
import pytest

from app.serving.feedback import _debit_campaign_budget, record_feedback, update_profile_vector

UNIT_PROFILE = [1.0, 0.0, 0.0]
UNIT_AD = [0.0, 1.0, 0.0]


# --- update_profile_vector: pure math, no mocking needed ---


def test_update_profile_vector_like_nudges_toward_ad():
    """A like reaction moves the profile partway toward the ad's vector."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "like")
    # moved partway from [1,0,0] toward [0,1,0] -> some positive weight on both axes
    assert updated[0] < 1.0
    assert updated[1] > 0.0


def test_update_profile_vector_interested_nudges_more_than_like():
    """interested has a higher LEARNING_RATE than like, so it should move
    the profile further in the same direction for an identical ad/profile."""
    like_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, "like")
    interested_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, "interested")
    # interested has a higher learning rate, so it should move further along the
    # same direction -- i.e. end up with a larger y-component.
    assert interested_result[1] > like_result[1]


def test_update_profile_vector_dislike_moves_away_from_ad():
    """dislike has a negative LEARNING_RATE, so the profile's similarity to
    the disliked ad should decrease, not increase or stay flat."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "dislike")
    # negative rate -> cosine similarity to the ad should decrease (here, go
    # from orthogonal (0.0) to negative), not increase or stay the same.
    original_similarity = float(np.dot(UNIT_PROFILE, UNIT_AD))
    updated_similarity = float(np.dot(updated, UNIT_AD))
    assert updated_similarity < original_similarity


def test_update_profile_vector_result_is_unit_normalized():
    """The nudged vector is always re-normalized to unit length, so
    similarity comparisons stay consistent across rounds."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "interested")
    assert np.linalg.norm(updated) == pytest.approx(1.0)


def test_update_profile_vector_unknown_outcome_is_a_no_op_before_normalization():
    """An outcome string not in LEARNING_RATE defaults to rate 0.0 -- the
    profile is unchanged (aside from the normalization step, itself a no-op
    here since UNIT_PROFILE is already unit length)."""
    # rate defaults to 0.0 for an outcome not in LEARNING_RATE, so the profile
    # itself is unchanged except for normalization (which is already a no-op
    # here since UNIT_PROFILE is already unit length).
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "unknown")
    assert updated == pytest.approx(UNIT_PROFILE)


# --- _debit_campaign_budget: real DB, no mocking ---


def test_debit_campaign_budget_like_debits_flat_cost(db, campaign):
    """A like debits COST_PER_OUTCOME["like"] from the campaign's budget and
    leaves status untouched while budget remains."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "like")

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    assert campaign.status == "active"


def test_debit_campaign_budget_dislike_costs_nothing(db, campaign):
    """dislike isn't a billable engagement (COST_PER_OUTCOME["dislike"] == 0),
    so budget_spent stays untouched."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "dislike")

    db.refresh(campaign)
    assert campaign.budget_spent == 0.0


def test_debit_campaign_budget_exhausts_and_completes(db, campaign):
    """Once budget_spent reaches budget_total, the campaign auto-transitions
    to status=completed, making it ineligible for the next retrieval pass."""
    campaign.budget_total = 1.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "like")  # 0.50
    _debit_campaign_budget(db, campaign.id, "like")  # 1.00 -> exhausted

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(1.0)
    assert campaign.status == "completed"


# --- record_feedback: mock Pinecone (fetch_vector/update_vector), real DB ---


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_debits_budget_and_returns_new_vector(mock_fetch, mock_update, db, campaign):
    """End-to-end happy path: a like nudges the profile vector (via
    update_vector) and debits the campaign's budget in the same call."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    new_vector = record_feedback(db, "pytest-user", str(campaign.id), "like")

    assert new_vector is not None
    mock_update.assert_called_once()
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_raises_when_no_profile_exists(mock_fetch, mock_update, db, campaign):
    """No fallback to the ad's own vector -- feedback only ever fires on an ad
    that was actually served, which itself requires a profile to already
    exist, so a missing one here is a bug, not a legitimate cold-start case."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else None

    with pytest.raises(ValueError, match="no profile found"):
        record_feedback(db, "pytest-user-with-no-profile", str(campaign.id), "like")

    mock_update.assert_not_called()


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_raises_when_ad_not_found(mock_fetch, mock_update, db):
    """No vector at all for the given ad_id in the ads namespace -- the ad
    itself doesn't exist, so this should raise before touching the profile."""
    mock_fetch.return_value = None

    with pytest.raises(ValueError):
        record_feedback(db, "pytest-user", "999999999", "like")

    mock_update.assert_not_called()
