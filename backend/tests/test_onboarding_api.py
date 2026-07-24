from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdCandidate, CheckpointJudgment

client = TestClient(app)

_CANDIDATE = AdCandidate(
    ad_id="219", headline="Say Goodbye to Leaks!", description="Plumbing inspection.", category="home_repair",
    similarity_score=0.9,
)


def _fake_stream(deltas):
    """A fake OpenAI streaming response: an iterable of SimpleNamespace
    objects shaped like the real response.output_text.delta events."""
    return [SimpleNamespace(type="response.output_text.delta", delta=d) for d in deltas]


@patch("app.serving.onboarding_api.retrieve_candidates", return_value=[])
@patch("app.serving.onboarding_api.upsert_vector")
@patch("app.serving.onboarding_api.embed_query")
@patch("app.serving.onboarding_api.fetch_vector", return_value=[0.1, 0.2, 0.3])
@patch(
    "app.serving.onboarding_api._judge_checkpoint",
    return_value=CheckpointJudgment(show_candidates=False, ready_to_finish=False, interest_summary="still vague"),
)
def test_checkpoint_skips_retrieval_when_show_candidates_false(
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve
):
    """A single vague reply shouldn't trigger an embed/seed/retrieve call --
    that's the whole point of the show_candidates gate."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"user_id": "pytest-user", "messages": [{"role": "user", "content": "need to fix some things"}]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["show_candidates"] is False
    assert data["candidates"] == []
    mock_embed_query.assert_not_called()
    mock_upsert_vector.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.serving.onboarding_api.retrieve_candidates", return_value=[_CANDIDATE])
@patch("app.serving.onboarding_api.upsert_vector")
@patch("app.serving.onboarding_api.embed_query", return_value=[[0.1, 0.2, 0.3]])
@patch("app.serving.onboarding_api.fetch_vector", return_value=None)
@patch(
    "app.serving.onboarding_api._judge_checkpoint",
    return_value=CheckpointJudgment(
        show_candidates=True, ready_to_finish=False, interest_summary="needs a plumber for a leaky sink"
    ),
)
def test_checkpoint_seeds_profile_on_first_show_candidates(
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve
):
    """First time show_candidates fires, no profile exists yet -- seeds one
    from the judged interest_summary before retrieving."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"user_id": "pytest-user", "messages": [{"role": "user", "content": "need a plumber"}]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["show_candidates"] is True
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["ad_id"] == "219"
    mock_embed_query.assert_called_once_with(["needs a plumber for a leaky sink"])
    mock_upsert_vector.assert_called_once()
    mock_retrieve.assert_called_once()


@patch("app.serving.onboarding_api.retrieve_candidates", return_value=[_CANDIDATE])
@patch("app.serving.onboarding_api.upsert_vector")
@patch("app.serving.onboarding_api.embed_query")
@patch("app.serving.onboarding_api.fetch_vector", return_value=[0.4, 0.5, 0.6])
@patch(
    "app.serving.onboarding_api._judge_checkpoint",
    return_value=CheckpointJudgment(show_candidates=True, ready_to_finish=True, interest_summary="plumbing focus"),
)
def test_checkpoint_skips_reseeding_when_profile_already_exists(
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve
):
    """A later round (profile already seeded in an earlier round) just
    retrieves fresh candidates -- doesn't re-embed/overwrite the profile."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"user_id": "pytest-user", "messages": [{"role": "user", "content": "just the sink really"}]},
    )

    assert resp.status_code == 200
    assert resp.json()["ready_to_finish"] is True
    mock_embed_query.assert_not_called()
    mock_upsert_vector.assert_not_called()
    mock_retrieve.assert_called_once()


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_streams_text_deltas(mock_get_client):
    """The streamed response body is the concatenation of delta chunks."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_stream(["Hi", " there", "!"])
    mock_get_client.return_value = mock_client

    resp = client.post("/onboarding/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert resp.status_code == 200
    assert resp.text == "Hi there!"


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_notes_show_candidates_in_instructions(mock_get_client):
    """When show_candidates=true, the model is told candidates are about to
    be shown -- so it can naturally acknowledge them without needing their
    specific details."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_stream(["ok"])
    mock_get_client.return_value = mock_client

    client.post(
        "/onboarding/chat",
        json={"messages": [{"role": "user", "content": "need a plumber"}], "show_candidates": True},
    )

    _, kwargs = mock_client.responses.create.call_args
    assert "showing the user a few candidate ads" in kwargs["instructions"]


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_omits_candidate_note_by_default(mock_get_client):
    """show_candidates defaults to False -- most turns are just conversation."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_stream(["ok"])
    mock_get_client.return_value = mock_client

    client.post("/onboarding/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    _, kwargs = mock_client.responses.create.call_args
    assert "showing the user a few candidate ads" not in kwargs["instructions"]
