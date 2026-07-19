from unittest.mock import MagicMock, patch

from app.retrieval import retrieve_candidates


@patch("app.retrieval.get_index")
@patch("app.retrieval.embed_query", return_value=[[0.1, 0.2, 0.3]])
def test_retrieve_candidates_maps_matches_to_ad_candidates(mock_embed_query, mock_get_index):
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [
            {
                "id": "ad-1",
                "score": 0.92,
                "metadata": {"title": "Leaky faucet repair kit", "description": "Fix drips fast", "category": "home_repair", "price": 19.99},
            }
        ]
    }
    mock_get_index.return_value = mock_index

    candidates = retrieve_candidates("user interested in home repair", top_k=5)

    assert len(candidates) == 1
    assert candidates[0].ad_id == "ad-1"
    assert candidates[0].similarity_score == 0.92
    mock_index.query.assert_called_once()
    _, kwargs = mock_index.query.call_args
    assert kwargs["namespace"] == "ads"
    assert kwargs["top_k"] == 5


@patch("app.retrieval.get_index")
@patch("app.retrieval.embed_query", return_value=[[0.1, 0.2, 0.3]])
def test_retrieve_candidates_empty_result(mock_embed_query, mock_get_index):
    mock_index = MagicMock()
    mock_index.query.return_value = {"matches": []}
    mock_get_index.return_value = mock_index

    candidates = retrieve_candidates("no matches expected", top_k=5)

    assert candidates == []
