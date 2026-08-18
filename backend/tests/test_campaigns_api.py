from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import Campaign
from tests.conftest import auth_header

client = TestClient(app)

_BASE_PAYLOAD = {
    "headline": "pytest headline",
    "description": "pytest description",
    "category": "hardware",
    "objective": "conversions",
    "budget_total": 100.0,
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
}


def _cleanup(db, campaign_ids: list[int]) -> None:
    for cid in campaign_ids:
        c = db.get(Campaign, cid)
        if c:
            db.delete(c)
    db.commit()


@patch("app.campaigns.api.campaign_review_queue")
def test_create_campaign_returns_pending_review_and_enqueues_job(mock_queue, db, advertiser_user):
    resp = client.post("/campaigns", json=_BASE_PAYLOAD, headers=auth_header(advertiser_user))

    try:
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending_review"
        assert data["budget_spent"] == 0.0
        assert data["user_id"] == advertiser_user.id

        mock_queue.enqueue.assert_called_once()
        _, enqueued_campaign_id = mock_queue.enqueue.call_args[0]
        assert enqueued_campaign_id == data["id"]
    finally:
        _cleanup(db, [resp.json()["id"]])


@patch("app.campaigns.api.campaign_review_queue")
def test_create_campaign_attributes_each_submission_to_its_own_account(mock_queue, db, advertiser_user, moderator_user):
    """No Advertiser entity to dedupe through anymore -- every submission is
    attributed to whichever account's token made the request, independent
    of any other submission (see docs/auth_plan.md)."""
    resp1 = client.post("/campaigns", json=_BASE_PAYLOAD, headers=auth_header(advertiser_user))
    resp2 = client.post("/campaigns", json=_BASE_PAYLOAD, headers=auth_header(moderator_user))

    try:
        assert resp1.json()["user_id"] == advertiser_user.id
        assert resp2.json()["user_id"] == moderator_user.id
    finally:
        _cleanup(db, [resp1.json()["id"], resp2.json()["id"]])


def test_list_campaigns_filters_by_status(db, campaign):
    campaign.status = "needs_review"
    db.commit()

    resp = client.get("/campaigns", params={"status": "needs_review"})
    assert resp.status_code == 200
    body = resp.json()
    assert campaign.id in [c["id"] for c in body["items"]]

    resp_other = client.get("/campaigns", params={"status": "rejected"})
    assert campaign.id not in [c["id"] for c in resp_other.json()["items"]]


def test_list_campaigns_filters_by_category(db, campaign):
    resp = client.get("/campaigns", params={"category": campaign.category})
    assert resp.status_code == 200
    assert campaign.id in [c["id"] for c in resp.json()["items"]]

    resp_other = client.get("/campaigns", params={"category": "not-a-real-category"})
    assert campaign.id not in [c["id"] for c in resp_other.json()["items"]]


def test_list_campaigns_searches_by_headline(db, campaign):
    campaign.headline = "Unique Pytest Search Headline"
    db.commit()

    resp = client.get("/campaigns", params={"search": "pytest search"})
    assert resp.status_code == 200
    assert campaign.id in [c["id"] for c in resp.json()["items"]]

    resp_miss = client.get("/campaigns", params={"search": "no such headline text"})
    assert campaign.id not in [c["id"] for c in resp_miss.json()["items"]]


def test_list_campaigns_sorts_by_headline_and_budget(db, advertiser_user):
    def _campaign(headline: str, budget_total: float) -> Campaign:
        c = Campaign(
            user_id=advertiser_user.id,
            headline=headline,
            description="pytest sort description",
            category="hardware",
            objective="conversions",
            budget_total=budget_total,
            budget_spent=0.0,
            start_date=date(2020, 1, 1),
            end_date=date(2099, 1, 1),
            excluded_categories=[],
            status="active",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    low = _campaign("Zzz Sort Test Low Budget", 5.0)
    high = _campaign("Aaa Sort Test High Budget", 50.0)
    try:
        by_headline = client.get(
            "/campaigns", params={"search": "Sort Test", "sort_by": "headline", "sort_dir": "asc"}
        ).json()["items"]
        headline_ids = [c["id"] for c in by_headline]
        assert headline_ids.index(high.id) < headline_ids.index(low.id)  # "Aaa" before "Zzz"

        by_budget = client.get(
            "/campaigns", params={"search": "Sort Test", "sort_by": "budget_total", "sort_dir": "desc"}
        ).json()["items"]
        budget_ids = [c["id"] for c in by_budget]
        assert budget_ids.index(high.id) < budget_ids.index(low.id)  # 50 before 5
    finally:
        db.delete(low)
        db.delete(high)
        db.commit()


def test_list_campaigns_paginates(db, campaign):
    resp = client.get("/campaigns", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["total"] >= 1
    assert body["total_pages"] >= 1


def test_moderate_campaign_approve_activates(db, campaign, moderator_user):
    campaign.status = "needs_review"
    db.commit()

    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "approved", "reason": "looks fine to a human"},
        headers=auth_header(moderator_user),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["reviewed_by"] == moderator_user.display_name
    assert data["review_reason"] == "looks fine to a human"


def test_moderate_campaign_reject_sets_status(db, campaign, moderator_user):
    campaign.status = "needs_review"
    db.commit()

    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "rejected", "reason": "violates policy"},
        headers=auth_header(moderator_user),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_moderate_campaign_wrong_status_returns_409(db, campaign, moderator_user):
    # campaign fixture defaults to status="active", not needs_review
    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "approved", "reason": "x"},
        headers=auth_header(moderator_user),
    )
    assert resp.status_code == 409


def test_moderate_campaign_not_found_returns_404(moderator_user):
    resp = client.post(
        "/campaigns/999999999/moderate",
        json={"outcome": "approved", "reason": "x"},
        headers=auth_header(moderator_user),
    )
    assert resp.status_code == 404


def test_create_campaign_rejects_end_user_role(user):
    """The actual point of this whole phase -- an end_user (the default
    role) cannot submit a campaign, and by the same mechanism could not
    reach /moderate either (see test below)."""
    resp = client.post("/campaigns", json=_BASE_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 403


def test_moderate_campaign_rejects_non_moderator_role(db, campaign, advertiser_user):
    """The urgent gap this phase closes: previously anyone could moderate
    any campaign. An advertiser (not a moderator) must be rejected too --
    this isn't just "logged in", it's specifically role-gated."""
    campaign.status = "needs_review"
    db.commit()

    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "approved", "reason": "x"},
        headers=auth_header(advertiser_user),
    )
    assert resp.status_code == 403
