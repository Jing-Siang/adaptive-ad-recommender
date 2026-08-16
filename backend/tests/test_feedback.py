import threading
import time
from datetime import date
from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.models import Campaign, Reaction
from app.serving.feedback import (
    COST_PER_OUTCOME,
    LEARNING_RATE,
    _debit_campaign_budget,
    clear_feedback,
    record_feedback,
    update_profile_vector,
)

UNIT_PROFILE = [1.0, 0.0, 0.0]
UNIT_AD = [0.0, 1.0, 0.0]


def _clear_reaction(db, user_id: int, campaign_id: int) -> None:
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
def test_record_feedback_first_reaction_applies_full_nudge_and_debit(mock_fetch, mock_update, db, campaign, user):
    """A brand-new reaction (no prior Reaction row) behaves like the old
    single-outcome path: full nudge, full debit."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    new_vector = record_feedback(db, str(user.id), str(campaign.id), "like")

    assert new_vector is not None
    mock_update.assert_called_once()
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)

    reaction = db.query(Reaction).filter_by(user_id=user.id, campaign_id=campaign.id).one()
    assert reaction.reaction == "like"

    _clear_reaction(db, user.id, campaign.id)


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_same_reaction_twice_is_a_true_noop(mock_fetch, mock_update, db, campaign, user):
    """Re-clicking the same reaction must not stack a second nudge/debit on
    top of the first -- this is the exact bug the reactions table fixes."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, str(user.id), str(campaign.id), "like")
    record_feedback(db, str(user.id), str(campaign.id), "like")

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)
    mock_update.assert_called_once()  # only the first call touched the profile vector

    reactions = db.query(Reaction).filter_by(user_id=user.id, campaign_id=campaign.id).all()
    assert len(reactions) == 1

    _clear_reaction(db, user.id, campaign.id)


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_switching_applies_net_delta_and_can_refund(mock_fetch, mock_update, db, campaign, user):
    """Switching from interested ($2.00) to like ($0.50) refunds the
    difference (-$1.50), not a second full $0.50 charge on top of $2.00."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, str(user.id), str(campaign.id), "interested")
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(2.00)

    record_feedback(db, str(user.id), str(campaign.id), "like")
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.50)

    reactions = db.query(Reaction).filter_by(user_id=user.id, campaign_id=campaign.id).all()
    assert len(reactions) == 1  # updated in place, not a second row
    assert reactions[0].reaction == "like"

    _clear_reaction(db, user.id, campaign.id)


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


# --- clear_feedback: mock Pinecone (fetch_vector/update_vector), real DB ---


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_clear_feedback_reverses_nudge_and_refunds_budget(mock_fetch, mock_update, db, campaign, user):
    """Removing an existing reaction is the exact inverse of applying it --
    full refund, and the profile nudge should cancel back out."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, str(user.id), str(campaign.id), "interested")
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(2.00)

    result = clear_feedback(db, str(user.id), str(campaign.id))

    assert result is not None
    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.0)

    reactions = db.query(Reaction).filter_by(user_id=user.id, campaign_id=campaign.id).all()
    assert reactions == []


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_clear_feedback_is_a_noop_when_nothing_to_remove(mock_fetch, mock_update, db, campaign, user):
    """Clearing a reaction that was never set (or already cleared) touches
    nothing -- no Pinecone write, no budget change, returns None."""
    campaign.budget_total = 10.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    result = clear_feedback(db, str(user.id), str(campaign.id))

    assert result is None
    mock_update.assert_not_called()
    db.refresh(campaign)
    assert campaign.budget_spent == 0.0


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_clear_feedback_revives_completed_campaign_on_refund(mock_fetch, mock_update, db, campaign, user):
    """Removing the reaction that exhausted a campaign's budget should
    refund it back under budget and revert status to active, same as a
    switch-to-cheaper-outcome refund does."""
    campaign.budget_total = 2.0
    campaign.budget_spent = 0.0
    db.commit()

    mock_fetch.side_effect = lambda vector_id, namespace: UNIT_AD if namespace == "ads" else UNIT_PROFILE

    record_feedback(db, str(user.id), str(campaign.id), "interested")
    db.refresh(campaign)
    assert campaign.status == "completed"

    clear_feedback(db, str(user.id), str(campaign.id))

    db.refresh(campaign)
    assert campaign.budget_spent == pytest.approx(0.0)
    assert campaign.status == "active"


@patch("app.serving.feedback.update_vector")
@patch("app.serving.feedback.fetch_vector")
def test_record_feedback_serializes_profile_vector_updates_for_same_user(mock_fetch, mock_update, db, campaign, advertiser, user):
    """Two genuinely concurrent reactions from the same user (different ads,
    separate DB connections/threads) must not both fetch the profile vector
    before either writes back -- that's the race documented in
    docs/future_ideas.md. Asserts real mutual exclusion via a Postgres
    advisory lock, not just that the code runs without error."""
    campaign_b = Campaign(
        advertiser_id=advertiser.id,
        headline="Second campaign for concurrency test",
        description="pytest",
        category="hardware",
        objective="conversions",
        budget_total=10.0,
        budget_spent=0.0,
        start_date=date(2020, 1, 1),
        end_date=date(2099, 1, 1),
        excluded_categories=[],
        status="active",
    )
    db.add(campaign_b)
    db.commit()
    db.refresh(campaign_b)

    events: list[tuple[str, int]] = []
    events_lock = threading.Lock()

    def fetch_side_effect(vector_id, namespace):
        if namespace == "users":
            with events_lock:
                events.append(("fetch_start", threading.get_ident()))
            time.sleep(0.05)  # widen the window -- would expose the race without the lock
        return UNIT_AD if namespace == "ads" else UNIT_PROFILE

    def update_side_effect(vector_id, values, namespace):
        with events_lock:
            events.append(("write", threading.get_ident()))

    mock_fetch.side_effect = fetch_side_effect
    mock_update.side_effect = update_side_effect

    def worker(ad_id: int, outcome: str) -> None:
        session = SessionLocal()
        try:
            record_feedback(session, str(user.id), str(ad_id), outcome)
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=(campaign.id, "like"))
    t2 = threading.Thread(target=worker, args=(campaign_b.id, "interested"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(events) == 4, f"expected 2 fetch_start + 2 write events, got {events}"

    # Mutual exclusion: no thread's fetch_start may occur while another
    # thread's fetch->write window is still open.
    open_thread = None
    for event_type, tid in events:
        if event_type == "fetch_start":
            assert open_thread is None, (
                "a second thread fetched the profile vector while another thread's "
                f"fetch->write window was still open -- events were {events}"
            )
            open_thread = tid
        else:
            assert open_thread == tid
            open_thread = None

    _clear_reaction(db, user.id, campaign.id)
    _clear_reaction(db, user.id, campaign_b.id)
    db.delete(campaign_b)
    db.commit()
