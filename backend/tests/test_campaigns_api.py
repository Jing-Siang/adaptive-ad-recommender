from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import Advertiser, Campaign

client = TestClient(app)

_BASE_PAYLOAD = {
    "advertiser_name": "pytest Advertiser",
    "headline": "pytest headline",
    "description": "pytest description",
    "category": "hardware",
    "objective": "conversions",
    "budget_total": 100.0,
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
}


def _cleanup(db, campaign_ids: list[int], advertiser_name: str) -> None:
    for cid in campaign_ids:
        c = db.get(Campaign, cid)
        if c:
            db.delete(c)
    db.commit()
    adv = db.query(Advertiser).filter_by(name=advertiser_name).first()
    if adv:
        db.delete(adv)
        db.commit()


@patch("app.campaigns.api.campaign_review_queue")
def test_create_campaign_returns_pending_review_and_enqueues_job(mock_queue, db):
    resp = client.post("/campaigns", json={**_BASE_PAYLOAD, "advertiser_name": "pytest Advertiser A"})

    try:
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending_review"
        assert data["budget_spent"] == 0.0

        mock_queue.enqueue.assert_called_once()
        _, enqueued_campaign_id = mock_queue.enqueue.call_args[0]
        assert enqueued_campaign_id == data["id"]
    finally:
        _cleanup(db, [resp.json()["id"]], "pytest Advertiser A")


@patch("app.campaigns.api.campaign_review_queue")
def test_create_campaign_reuses_existing_advertiser_by_name(mock_queue, db):
    payload = {**_BASE_PAYLOAD, "advertiser_name": "pytest Advertiser B"}
    resp1 = client.post("/campaigns", json=payload)
    resp2 = client.post("/campaigns", json={**payload, "headline": "second campaign"})

    try:
        assert resp1.json()["advertiser_id"] == resp2.json()["advertiser_id"]
    finally:
        _cleanup(db, [resp1.json()["id"], resp2.json()["id"]], "pytest Advertiser B")


def test_get_campaign_not_found_returns_404():
    resp = client.get("/campaigns/999999999")
    assert resp.status_code == 404


def test_get_campaign_returns_full_record(db, campaign):
    resp = client.get(f"/campaigns/{campaign.id}")
    assert resp.status_code == 200
    assert resp.json()["headline"] == campaign.headline


def test_list_campaigns_filters_by_status(db, campaign):
    campaign.status = "needs_review"
    db.commit()

    resp = client.get("/campaigns", params={"status": "needs_review"})
    assert resp.status_code == 200
    assert campaign.id in [c["id"] for c in resp.json()]

    resp_other = client.get("/campaigns", params={"status": "rejected"})
    assert campaign.id not in [c["id"] for c in resp_other.json()]


def test_moderate_campaign_approve_activates(db, campaign):
    campaign.status = "needs_review"
    db.commit()

    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "approved", "reason": "looks fine to a human", "reviewed_by": "pytest-moderator"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["reviewed_by"] == "pytest-moderator"
    assert data["review_reason"] == "looks fine to a human"


def test_moderate_campaign_reject_sets_status(db, campaign):
    campaign.status = "needs_review"
    db.commit()

    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "rejected", "reason": "violates policy", "reviewed_by": "pytest-moderator"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_moderate_campaign_wrong_status_returns_409(db, campaign):
    # campaign fixture defaults to status="active", not needs_review
    resp = client.post(
        f"/campaigns/{campaign.id}/moderate",
        json={"outcome": "approved", "reason": "x", "reviewed_by": "pytest-moderator"},
    )
    assert resp.status_code == 409


def test_moderate_campaign_not_found_returns_404():
    resp = client.post(
        "/campaigns/999999999/moderate",
        json={"outcome": "approved", "reason": "x", "reviewed_by": "pytest-moderator"},
    )
    assert resp.status_code == 404
