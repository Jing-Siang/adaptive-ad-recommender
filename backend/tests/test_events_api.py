from unittest.mock import ANY, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import Event

client = TestClient(app)


def test_log_impression_inserts_event_row(db, campaign):
    """POST /events/impression is a pure DB insert -- no Pinecone/LLM call
    involved, so nothing needs mocking here."""
    resp = client.post("/events/impression", json={"user_id": "pytest-user", "ad_id": str(campaign.id)})

    assert resp.status_code == 201
    event = db.query(Event).filter_by(campaign_id=campaign.id, event_type="impression").first()
    assert event is not None
    assert event.user_id == "pytest-user"

    db.delete(event)
    db.commit()


@patch("app.serving.events_api.record_feedback")
def test_react_inserts_event_row_and_calls_record_feedback(mock_record_feedback, db, campaign):
    """A like reaction both logs an events row and delegates the profile-
    nudge/budget-debit side to record_feedback with the right plain args."""
    resp = client.post(
        "/events/reaction",
        json={"user_id": "pytest-user", "ad_id": str(campaign.id), "reaction": "like"},
    )

    assert resp.status_code == 201
    mock_record_feedback.assert_called_once_with(ANY, "pytest-user", str(campaign.id), "like")

    event = db.query(Event).filter_by(campaign_id=campaign.id, event_type="like").first()
    assert event is not None

    db.delete(event)
    db.commit()


@patch("app.serving.events_api.record_feedback", side_effect=ValueError("no profile found for user 'x'"))
def test_react_returns_404_and_does_not_log_when_no_profile(mock_record_feedback, db, campaign):
    """record_feedback's ValueError (no profile) surfaces as 404, and the
    event row is never written since the reaction never actually succeeded."""
    resp = client.post(
        "/events/reaction",
        json={"user_id": "pytest-user-with-no-profile", "ad_id": str(campaign.id), "reaction": "like"},
    )

    assert resp.status_code == 404
    event = db.query(Event).filter_by(campaign_id=campaign.id, event_type="like").first()
    assert event is None


@patch("app.serving.events_api.clear_feedback", return_value=[1.0, 0.0, 0.0])
def test_unreact_returns_removed_when_a_reaction_existed(mock_clear_feedback, campaign):
    """DELETE /events/reaction reports "removed" (not "recorded") and
    delegates entirely to clear_feedback -- no Event row involved."""
    resp = client.request(
        "DELETE",
        "/events/reaction",
        json={"user_id": "pytest-user", "ad_id": str(campaign.id)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "removed"}
    mock_clear_feedback.assert_called_once_with(ANY, "pytest-user", str(campaign.id))


@patch("app.serving.events_api.clear_feedback", return_value=None)
def test_unreact_returns_no_reaction_when_nothing_to_remove(mock_clear_feedback, campaign):
    """clear_feedback's None (nothing was there) surfaces as status
    "no_reaction", not an error -- removing an already-cleared reaction is
    a legitimate no-op, not a failure."""
    resp = client.request(
        "DELETE",
        "/events/reaction",
        json={"user_id": "pytest-user-never-reacted", "ad_id": str(campaign.id)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "no_reaction"}


@patch("app.serving.events_api.clear_feedback", side_effect=ValueError("no profile found for user 'x'"))
def test_unreact_returns_404_when_no_profile(mock_clear_feedback, campaign):
    """Same 404 contract as react() for a missing profile."""
    resp = client.request(
        "DELETE",
        "/events/reaction",
        json={"user_id": "pytest-user-with-no-profile", "ad_id": str(campaign.id)},
    )

    assert resp.status_code == 404


def test_report_requires_reason_when_category_is_other(campaign):
    """ReportRequest's model_validator rejects category="other" with no
    reason before the request ever reaches the handler."""
    resp = client.post(
        "/events/report",
        json={"user_id": "pytest-user", "ad_id": str(campaign.id), "category": "other"},
    )
    assert resp.status_code == 422


def test_report_flips_campaign_to_needs_review_after_threshold(db, campaign):
    """Three reports from three different users on the same campaign cross
    REPORT_THRESHOLD (counted from the events table, not a separate
    counter column) and auto-flip status to needs_review."""
    campaign.status = "active"
    db.commit()

    for i in range(3):
        resp = client.post(
            "/events/report",
            json={"user_id": f"pytest-reporter-{i}", "ad_id": str(campaign.id), "category": "spam"},
        )
        assert resp.status_code == 201

    db.refresh(campaign)
    assert campaign.status == "needs_review"

    db.query(Event).filter_by(campaign_id=campaign.id, event_type="report").delete()
    db.commit()
