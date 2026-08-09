from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy import delete

from app.models import Reaction
from app.serving.feedback import COST_PER_OUTCOME, LEARNING_RATE, _debit_campaign_budget, record_feedback, update_profile_vector

UNIT_PROFILE = [1.0, 0.0, 0.0]
UNIT_AD = [0.0, 1.0, 0.0]


def _clear_reaction(db, user_id: str, campaign_id: int) -> None:
    """record_feedback upserts a real Reaction row via raw SQL -- clean it up
    before the campaign fixture's teardown deletes the campaign, or that
    delete hits the same FK-violation shape as Event rows do."""
    db.execute(delete(Reaction).where(Reaction.user_id == user_id, Reaction.campaign_id == campaign_id))
    db.commit()


# --- update_profile_vector: pure math, no mocking needed ---


def test_update_profile_vector_positive_rate_nudges_toward_ad():
    """A positive rate (e.g. LEARNING_RATE["like"]) moves the profile partway
    toward the ad's vector."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, LEARNING_RATE["like"])
    # moved partway from [1,0,0] toward [0,1,0] -> some positive weight on both axes
    assert updated[0] < 1.0
    assert updated[1] > 0.0


def test_update_profile_vector_larger_rate_nudges_more():
    """A larger rate should move the profile further in the same direction
    for an identical ad/profile."""
    like_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, LEARNING_RATE["like"])
    interested_result = update_profile_vector(UNIT_PROFILE, UNIT_AD, LEARNING_RATE["interested"])
    assert interested_result[1] > like_result[1]


def test_update_profile_vector_negative_rate_moves_away_from_ad():
    """A negative rate (e.g. LEARNING_RATE["dislike"]) decreases similarity to
    the ad, not increases or leaves it flat."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, LEARNING_RATE["dislike"])
    original_similarity = float(np.dot(UNIT_PROFILE, UNIT_AD))
    updated_similarity = float(np.dot(updated, UNIT_AD))
    assert updated_similarity < original_similarity


def test_update_profile_vector_result_is_unit_normalized():
    """The nudged vector is always re-normalized to unit length, so
    similarity comparisons stay consistent across rounds."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, LEARNING_RATE["interested"])
    assert np.linalg.norm(updated) == pytest.approx(1.0)


def test_update_profile_vector_zero_rate_is_a_no_op_before_normalization():
    """A rate of 0.0 (e.g. no net change across a switch) leaves the profile
    unchanged aside from normalization (itself a no-op here since
    UNIT_PROFILE is already unit length)."""
    updated = update_profile_vector(UNIT_PROFILE, UNIT_AD, 0.0)
    assert updated == pytest.approx(UNIT_PROFILE)


# --- _debit_campaign_budget: real DB, no mocking ---


def test_debit_campaign_budget_positive_delta_debits_budget(db, campaign):
    """A positive cost_delta (e.g. a fresh "like") debits that amount from
    the campaign's budget and leaves status untouched while budget remains."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, COST_PER_OUTCOME["like"])

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    assert campaign.status == "active"


def test_debit_campaign_budget_zero_delta_is_a_no_op(db, campaign):
    """A zero cost_delta (e.g. dislike, or a same-reaction no-op) leaves
    budget_spent untouched."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, 0.0)

    db.refresh(campaign)
    assert campaign.budget_spent == 0.0


def test_debit_campaign_budget_exhausts_and_completes(db, campaign):
    """Once budget_spent reaches budget_total, the campaign auto-transitions
    to status=completed, making it ineligible for the next retrieval pass."""
    campaign.budget_total = 1.0
    campaign.budget_spent = 0.0
    db.commit()

    _debit_campaign_budget(db, campaign.id, 0.50)
    _debit_campaign_budget(db, campaign.id, 0.50)  # -> 1.00, exhausted

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(1.0)
    assert campaign.status == "completed"


def test_debit_campaign_budget_negative_delta_refunds_and_revives_completed(db, campaign):
    """A refund (negative cost_delta, e.g. switching interested -> like) can
    drop spend back under budget -- the symmetric case of the exhaustion
    check above, safe because the Kafka CDC consumer re-syncs any status
    change automatically."""
    campaign.budget_total = 1.0
    campaign.budget_spent = 1.0
    campaign.status = "completed"
    db.commit()

    _debit_campaign_budget(db, campaign.id, -0.50)

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    assert campaign.status == "active"


# --- record_feedback: mock Pinecone (fetch_vector/update_vector), real DB ---


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_first_reaction_applies_full_nudge_and_debit(mock_fetch, mock_update, db, campaign):
    """A brand-new reaction (no prior Reaction row) behaves like the old
    single-outcome path: full nudge, full debit."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    new_vector = record_feedback(db, "pytest-user", str(campaign.id), "like")

    assert new_vector is not None
    mock_update.assert_called_once()
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)

    reaction = db.query(Reaction).filter_by(user_id="pytest-user", campaign_id=campaign.id).one()
    assert reaction.reaction == "like"

    _clear_reaction(db, "pytest-user", campaign.id)


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_same_reaction_twice_is_a_true_noop(mock_fetch, mock_update, db, campaign):
    """Re-clicking the same reaction must not stack a second nudge/debit on
    top of the first -- this is the exact bug the reactions table fixes."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, "pytest-user", str(campaign.id), "like")
    record_feedback(db, "pytest-user", str(campaign.id), "like")

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    mock_update.assert_called_once()  # only the first call touched the profile vector

    reactions = db.query(Reaction).filter_by(user_id="pytest-user", campaign_id=campaign.id).all()
    assert len(reactions) == 1

    _clear_reaction(db, "pytest-user", campaign.id)


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_switching_applies_net_delta_and_can_refund(mock_fetch, mock_update, db, campaign):
    """Switching from interested ($2.00) to like ($0.50) refunds the
    difference (-$1.50), not a second full $0.50 charge on top of $2.00."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, "pytest-user", str(campaign.id), "interested")
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(2.00)

    record_feedback(db, "pytest-user", str(campaign.id), "like")
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)

    reactions = db.query(Reaction).filter_by(user_id="pytest-user", campaign_id=campaign.id).all()
    assert len(reactions) == 1  # updated in place, not a second row
    assert reactions[0].reaction == "like"

    _clear_reaction(db, "pytest-user", campaign.id)


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
