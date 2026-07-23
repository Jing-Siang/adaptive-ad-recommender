from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdCandidate, GuardrailResult, RankedAd

client = TestClient(app)

_CANDIDATES = [
    AdCandidate(ad_id="1", headline="A", description="desc A", category="home_repair", similarity_score=0.9),
    AdCandidate(ad_id="2", headline="B", description="desc B", category="alcohol", similarity_score=0.8),
]
_RANKINGS = [
    RankedAd(ad_id="1", relevance_score=0.7, justification="matches home repair interest"),
    RankedAd(ad_id="2", relevance_score=0.95, justification="matches drink preference"),
]


def _guardrail_side_effect(ad, context_categories):
    if ad.category == "alcohol":
        return GuardrailResult(ad_id=ad.ad_id, allowed=False, reason="excluded category")
    return GuardrailResult(ad_id=ad.ad_id, allowed=True)


@patch("app.serving.api.check_guardrails", side_effect=_guardrail_side_effect)
@patch("app.serving.api.rerank", return_value=_RANKINGS)
@patch("app.serving.api.retrieve_candidates", return_value=_CANDIDATES)
def test_recommend_batch_sorts_by_relevance_and_filters_guardrail_blocked(mock_retrieve, mock_rerank, mock_guardrail):
    resp = client.post("/recommend/batch", json={"user_id": "pytest-user", "batch_size": 10})

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "pytest-user"
    # ad_id "2" ranks higher (0.95) but is guardrail-blocked (alcohol), so only "1" remains
    assert [item["ad_id"] for item in data["items"]] == ["1"]
    assert data["items"][0]["relevance_score"] == 0.7
    assert data["items"][0]["justification"] == "matches home repair interest"


@patch("app.serving.api.retrieve_candidates", side_effect=ValueError("no profile found for user 'x'"))
def test_recommend_batch_returns_404_when_no_profile(mock_retrieve):
    resp = client.post("/recommend/batch", json={"user_id": "x", "batch_size": 10})
    assert resp.status_code == 404


@patch("app.serving.api.retrieve_candidates", return_value=[])
def test_recommend_batch_returns_404_when_no_candidates(mock_retrieve):
    resp = client.post("/recommend/batch", json={"user_id": "pytest-user", "batch_size": 10})
    assert resp.status_code == 404


def test_recommend_batch_rejects_batch_size_over_cap():
    resp = client.post("/recommend/batch", json={"user_id": "pytest-user", "batch_size": 51})
    assert resp.status_code == 422
