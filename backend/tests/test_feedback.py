from unittest.mock import patch

import numpy as np
import pytest

from app.schemas import FeedbackEvent
from app.serving.feedback import _debit_campaign_budget, record_feedback, update_profile_vector

UNIT_PROFILE = [1.0, 0.0, 0.0]
UNIT_AD = [0.0, 1.0, 0.0]


# --- update_profile_vector: pure math, no mocking needed ---


def test_update_profile_vector_click_nudges_toward_ad():
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "click")
    # moved partway from [1,0,0] toward [0,1,0] -> some positive weight on both axes
    assert updated[0] < 1.0
    assert updated[1] > 0.0


def test_update_profile_vector_conversion_nudges_more_than_click():
    click_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, "click")
    conversion_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, "conversion")
    # conversion has a higher learning rate, so it should move further along the
    # same direction -- i.e. end up with a larger y-component.
    assert conversion_result[1] > click_result[1]


def test_update_profile_vector_no_click_moves_away_from_ad():
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "no_click")
    # negative rate -> cosine similarity to the ad should decrease (here, go
    # from orthogonal (0.0) to negative), not increase or stay the same.
    original_similarity = float(np.dot(UNIT_PROFILE, UNIT_AD))
    updated_similarity = float(np.dot(updated, UNIT_AD))
    assert updated_similarity < original_similarity


def test_update_profile_vector_result_is_unit_normalized():
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "conversion")
    assert np.linalg.norm(updated) == pytest.approx(1.0)


def test_update_profile_vector_unknown_outcome_is_a_no_op_before_normalization():
    # rate defaults to 0.0 for an outcome not in LEARNING_RATE, so the profile
    # itself is unchanged except for normalization (which is already a no-op
    # here since UNIT_PROFILE is already unit length).
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, "unknown")
    assert updated == pytest.approx(UNIT_PROFILE)


# --- _debit_campaign_budget: real DB, no mocking ---


def test_debit_campaign_budget_click_debits_flat_cost(db, campaign):
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "click")

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    assert campaign.status == "active"


def test_debit_campaign_budget_no_click_costs_nothing(db, campaign):
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "no_click")

    db.refresh(campaign)
    assert campaign.budget_spent == 0.0


def test_debit_campaign_budget_exhausts_and_completes(db, campaign):
    campaign.budget_total = 1.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, "click")  # 0.50
    _debit_campaign_budget(db, campaign.id, "click")  # 1.00 -> exhausted

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(1.0)
    assert campaign.status == "completed"


# --- record_feedback: mock Pinecone (fetch_vector/upsert_vector), real DB ---


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_debits_budget_and_returns_new_vector(mock_fetch, mock_update, db, campaign):
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE
    event = FeedbackEvent(user_id="pytest-user", ad_id=str(campaign.id), outcome="click")

    new_vector = record_feedback(db, event)

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
    event = FeedbackEvent(user_id="pytest-user-with-no-profile", ad_id=str(campaign.id), outcome="click")

    with pytest.raises(ValueError, match="no profile found"):
        record_feedback(db, event)

    mock_update.assert_not_called()


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_raises_when_ad_not_found(mock_fetch, mock_update, db):
    mock_fetch.return_value = None
    event = FeedbackEvent(user_id="pytest-user", ad_id="999999999", outcome="click")

    with pytest.raises(ValueError):
        record_feedback(db, event)

    mock_update.assert_not_called()
