import json

import pytest
from unittest.mock import MagicMock, patch

from app.ranking import rerank
from app.schemas import AdCandidate

CANDIDATE = AdCandidate(
    ad_id="ad-1", title="Plumber hotline", description="24/7 emergency plumbing", category="services", similarity_score=0.88
)


def _mock_message(text: str) -> MagicMock:
    message = MagicMock()
    message.content = [MagicMock(text=text)]
    return message


@patch("app.ranking._call_claude")
def test_rerank_parses_valid_structured_output(mock_call_claude):
    mock_call_claude.return_value = json.dumps(
        {"rankings": [{"ad_id": "ad-1", "relevance_score": 0.95, "justification": "matches urgent intent"}]}
    )

    rankings = rerank("user reading about a leaky faucet", [CANDIDATE])

    assert len(rankings) == 1
    assert rankings[0].ad_id == "ad-1"
    assert rankings[0].relevance_score == 0.95


@patch("app.ranking._call_claude")
def test_rerank_raises_after_repeated_invalid_output(mock_call_claude):
    mock_call_claude.return_value = "not json at all"

    with pytest.raises(ValueError):
        rerank("some context", [CANDIDATE])

    assert mock_call_claude.call_count == 3


@patch("app.ranking._call_claude")
def test_rerank_rejects_out_of_range_relevance_score(mock_call_claude):
    mock_call_claude.return_value = json.dumps(
        {"rankings": [{"ad_id": "ad-1", "relevance_score": 1.5, "justification": "bad score"}]}
    )

    with pytest.raises(ValueError):
        rerank("some context", [CANDIDATE])
