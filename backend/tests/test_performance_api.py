from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.models import Event
from app.serving.performance_api import _rate
from tests.conftest import auth_header

client = TestClient(app)


def test_rate_helper_divides_normally():
    """Plain numerator/denominator division."""
    assert _rate(1, 4) == 0.25


def test_rate_helper_returns_zero_for_zero_denominator():
    """No impressions yet -- a rate is 0.0, not a ZeroDivisionError."""
    assert _rate(5, 0) == 0.0


def test_get_performance_totals_reflects_inserted_events(db, campaign, advertiser_user):
    """Inserting known impression/like/dislike/interested counts moves the
    global totals by exactly that delta, and the derived rate fields are
    self-consistent with whatever the resulting totals are (avoids asserting
    exact totals, since /performance aggregates across the whole table,
    which other tests may also be writing to)."""
    headers = auth_header(advertiser_user)
    before = client.get("/performance", headers=headers).json()["totals"]

    events = [
        Event(user_id="pytest-u1", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="pytest-u2", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="pytest-u1", campaign_id=campaign.id, event_type="like"),
        Event(user_id="pytest-u2", campaign_id=campaign.id, event_type="dislike"),
        Event(user_id="pytest-u1", campaign_id=campaign.id, event_type="interested"),
    ]
    db.add_all(events)
    db.commit()

    try:
        after = client.get("/performance", headers=headers).json()["totals"]

        assert after["impressions"] == before["impressions"] + 2
        assert after["likes"] == before["likes"] + 1
        assert after["dislikes"] == before["dislikes"] + 1
        assert after["conversions"] == before["conversions"] + 1
        assert after["ctr"] == after["conversions"] / after["impressions"]
        assert after["engagement_rate"] == after["likes"] / after["impressions"]
        assert after["dislike_rate"] == after["dislikes"] / after["impressions"]
        assert after["avg_cpa"] == after["total_spend"] / after["conversions"]
    finally:
        for e in events:
            db.delete(e)
        db.commit()


def test_get_performance_trend_buckets_by_day(db, campaign, advertiser_user):
    """Events on two distinct (far-past, collision-free) dates produce two
    separate trend points with the right per-day impression/conversion/CTR
    breakdown."""
    headers = auth_header(advertiser_user)
    day_one = date(2020, 6, 15)
    day_two = date(2020, 6, 16)
    events = [
        Event(user_id="u1", campaign_id=campaign.id, event_type="impression", created_at=day_one),
        Event(user_id="u2", campaign_id=campaign.id, event_type="impression", created_at=day_one),
        Event(user_id="u1", campaign_id=campaign.id, event_type="interested", created_at=day_one),
        Event(user_id="u1", campaign_id=campaign.id, event_type="impression", created_at=day_two),
    ]
    db.add_all(events)
    db.commit()

    try:
        trend = client.get("/performance", headers=headers).json()["trend"]
        by_day = {point["date"]: point for point in trend}

        assert by_day["2020-06-15"] == {"date": "2020-06-15", "impressions": 2, "conversions": 1, "ctr": 0.5}
        assert by_day["2020-06-16"] == {"date": "2020-06-16", "impressions": 1, "conversions": 0, "ctr": 0.0}
    finally:
        for e in events:
            db.delete(e)
        db.commit()


def test_get_performance_per_campaign_breakdown(db, campaign, advertiser_user):
    """A campaign's row in the breakdown table has exactly the counts and
    CTR its own events produce, independent of any other campaign's data."""
    headers = auth_header(advertiser_user)
    events = [
        Event(user_id="u1", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="u2", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="u3", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="u4", campaign_id=campaign.id, event_type="impression"),
        Event(user_id="u1", campaign_id=campaign.id, event_type="interested"),
        Event(user_id="u2", campaign_id=campaign.id, event_type="report", report_category="spam"),
    ]
    db.add_all(events)
    db.commit()

    try:
        campaigns = client.get("/performance", headers=headers).json()["campaigns"]
        row = next(c for c in campaigns if c["campaign_id"] == campaign.id)

        assert row["headline"] == campaign.headline
        assert row["status"] == campaign.status
        assert row["impressions"] == 4
        assert row["conversions"] == 1
        assert row["reports"] == 1
        assert row["ctr"] == 0.25
        assert row["spend"] == campaign.budget_spent
    finally:
        for e in events:
            db.delete(e)
        db.commit()


def test_get_performance_includes_campaigns_with_zero_events(campaign, advertiser_user):
    """A campaign with no events at all still appears, with all-zero counts,
    rather than being silently omitted from the breakdown table."""
    campaigns = client.get("/performance", headers=auth_header(advertiser_user)).json()["campaigns"]
    row = next(c for c in campaigns if c["campaign_id"] == campaign.id)

    assert row["impressions"] == 0
    assert row["ctr"] == 0.0


def test_get_performance_rejects_end_user_role(user):
    """The point of gating this endpoint at all -- an end_user (the
    default role) is not part of the advertiser/moderator audience."""
    resp = client.get("/performance", headers=auth_header(user))
    assert resp.status_code == 403
