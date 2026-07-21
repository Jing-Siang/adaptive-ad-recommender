from unittest.mock import MagicMock, patch

import pytest

from app.ranking import rerank
from app.schemas import AdCandidate, RankedAd, RankingResponse

CANDIDATE = AdCandidate(
    ad_id="ad-1", headline="Plumber hotline", description="24/7 emergency plumbing", category="services", similarity_score=0.88
)


def _mock_parsed_response(ranking_response: RankingResponse) -> MagicMock:
    response = MagicMock()
    response.output_parsed = ranking_response
    return response


@patch("app.ranking._get_client")
def test_rerank_returns_parsed_rankings(mock_get_client):
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _mock_parsed_response(
        RankingResponse(rankings=[RankedAd(ad_id="ad-1", relevance_score=0.95, justification="matches urgent intent")])
    )
    mock_get_client.return_value = mock_client

    rankings = rerank("user reading about a leaky faucet", [CANDIDATE])

    assert len(rankings) == 1
    assert rankings[0].ad_id == "ad-1"
    assert rankings[0].relevance_score == 0.95
    _, kwargs = mock_client.responses.parse.call_args
    assert kwargs["text_format"] is RankingResponse


@patch("app.ranking._get_client")
def test_rerank_retries_on_transient_error_then_succeeds(mock_get_client):
    mock_client = MagicMock()
    mock_client.responses.parse.side_effect = [
        RuntimeError("transient API error"),
        _mock_parsed_response(
            RankingResponse(rankings=[RankedAd(ad_id="ad-1", relevance_score=0.7, justification="ok")])
        ),
    ]
    mock_get_client.return_value = mock_client

    rankings = rerank("some context", [CANDIDATE])

    assert rankings[0].relevance_score == 0.7
    assert mock_client.responses.parse.call_count == 2


@patch("app.ranking._get_client")
def test_rerank_raises_after_repeated_failures(mock_get_client):
    mock_client = MagicMock()
    mock_client.responses.parse.side_effect = RuntimeError("persistent API error")
    mock_get_client.return_value = mock_client

    with pytest.raises(RuntimeError):
        rerank("some context", [CANDIDATE])

    assert mock_client.responses.parse.call_count == 4
