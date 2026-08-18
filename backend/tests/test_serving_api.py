from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdCandidate, GuardrailResult
from tests.conftest import auth_header

client = TestClient(app)

# Already in similarity-descending order -- retrieve_candidates guarantees
# this (Pinecone returns nearest-first), and recommend_batch relies on it
# now that LLM re-ranking is disabled (see app/serving/api.py's docstring).
_CANDIDATES = [
    AdCandidate(ad_id="1", headline="A", description="desc A", category="alcohol", similarity_score=0.9),
    AdCandidate(ad_id="2", headline="B", description="desc B", category="home_repair", similarity_score=0.8),
]


def _guardrail_side_effect(ad, context_categories):
    if ad.category == "alcohol":
        return GuardrailResult(ad_id=ad.ad_id, allowed=False, reason="excluded category")
    return GuardrailResult(ad_id=ad.ad_id, allowed=True)


@patch("app.serving.api.check_guardrails", side_effect=_guardrail_side_effect)
@patch("app.serving.api.retrieve_candidates", return_value=_CANDIDATES)
def test_recommend_batch_orders_by_similarity_and_filters_guardrail_blocked(mock_retrieve, mock_guardrail, user):
    """Batch response preserves retrieve_candidates' similarity order, and a
    higher-similarity ad that fails the guardrail check is dropped rather
    than served."""
    resp = client.post("/recommend/batch", json={"batch_size": 10}, headers=auth_header(user))

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == str(user.id)
    # ad_id "1" has higher similarity (0.9) but is guardrail-blocked (alcohol), so only "2" remains
    assert [item["ad_id"] for item in data["items"]] == ["2"]
    assert data["items"][0]["relevance_score"] == 0.8
    assert data["items"][0]["justification"] == "Ranked by vector similarity to your profile."


@patch("app.serving.api.retrieve_candidates", side_effect=ValueError("no profile found for user 'x'"))
def test_recommend_batch_returns_404_when_no_profile(mock_retrieve, user):
    """retrieve_candidates' ValueError (no profile exists yet) surfaces as
    HTTP 404, same translation /recommend already does."""
    resp = client.post("/recommend/batch", json={"batch_size": 10}, headers=auth_header(user))
    assert resp.status_code == 404


@patch("app.serving.api.retrieve_candidates", return_value=[])
def test_recommend_batch_returns_404_when_no_candidates(mock_retrieve, user):
    """A profile exists but nothing eligible matched -- also a 404, distinct
    from the missing-profile case above but same status code."""
    resp = client.post("/recommend/batch", json={"batch_size": 10}, headers=auth_header(user))
    assert resp.status_code == 404


def test_recommend_batch_rejects_batch_size_over_cap(user):
    """batch_size is capped at 50 (Field(le=50)) to bound a single re-rank
    call's cost; exceeding it is a validation error, not a truncated batch."""
    resp = client.post("/recommend/batch", json={"batch_size": 51}, headers=auth_header(user))
    assert resp.status_code == 422


def test_recommend_batch_requires_auth():
    resp = client.post("/recommend/batch", json={"batch_size": 10})
    assert resp.status_code == 401
