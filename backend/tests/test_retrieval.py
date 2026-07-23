import pytest
from unittest.mock import MagicMock, patch

from app.serving.retrieval import retrieve_candidates


@patch("app.serving.retrieval._eligible_campaign_ids", return_value={1})
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_maps_matches_to_ad_candidates(mock_fetch_vector, mock_get_index, mock_eligible):
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [
            {
                "id": "1",
                "score": 0.92,
                "metadata": {
                    "headline": "Leaky faucet repair kit",
                    "description": "Fix drips fast",
                    "category": "home_repair",
                    "price": 19.99,
                },
            }
        ]
    }
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert len(candidates) == 1
    assert candidates[0].ad_id == "1"
    assert candidates[0].similarity_score == 0.92
    mock_index.query.assert_called_once()
    _, kwargs = mock_index.query.call_args
    assert kwargs["namespace"] == "ads"
    assert kwargs["top_k"] == 5 * 3  # oversampled, since some matches may be ineligible


@patch("app.serving.retrieval._eligible_campaign_ids", return_value=set())
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_empty_result(mock_fetch_vector, mock_get_index, mock_eligible):
    mock_index = MagicMock()
    mock_index.query.return_value = {"matches": []}
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert candidates == []


@patch("app.serving.retrieval._eligible_campaign_ids", return_value={2})
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_filters_out_ineligible_campaigns(mock_fetch_vector, mock_get_index, mock_eligible):
    """Campaign 1 is a closer vector match, but only campaign 2 is eligible
    (e.g. campaign 1's budget is exhausted or it's expired) -- it should be
    excluded even though Pinecone returned it."""
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [
            {"id": "1", "score": 0.95, "metadata": {"headline": "A", "description": "A", "category": "x"}},
            {"id": "2", "score": 0.80, "metadata": {"headline": "B", "description": "B", "category": "x"}},
        ]
    }
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert len(candidates) == 1
    assert candidates[0].ad_id == "2"


@patch("app.serving.retrieval.fetch_vector", return_value=None)
def test_retrieve_candidates_raises_when_no_profile_exists(mock_fetch_vector):
    mock_db = MagicMock()

    with pytest.raises(ValueError, match="no profile found"):
        retrieve_candidates(mock_db, "user-with-no-profile", top_k=5)
