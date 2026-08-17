from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.serving.onboarding_api as onboarding_api
from app.main import app
from app.schemas import AdCandidate, CheckpointJudgment
from tests.conftest import auth_header

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
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve, user
):
    """A single vague reply shouldn't trigger an embed/seed/retrieve call --
    that's the whole point of the show_candidates gate."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"messages": [{"role": "user", "content": "need to fix some things"}]},
        headers=auth_header(user),
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
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve, user
):
    """First time show_candidates fires, no profile exists yet -- seeds one
    from the judged interest_summary before retrieving."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"messages": [{"role": "user", "content": "need a plumber"}]},
        headers=auth_header(user),
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
    return_value=CheckpointJudgment(show_candidates=True, ready_to_finish=False, interest_summary="plumbing focus"),
)
def test_checkpoint_skips_reseeding_when_profile_already_exists(
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve, user
):
    """A later round (profile already seeded in an earlier round) just
    retrieves fresh candidates -- doesn't re-embed/overwrite the profile."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"messages": [{"role": "user", "content": "just the sink really"}]},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    assert resp.json()["ready_to_finish"] is False
    mock_embed_query.assert_not_called()
    mock_upsert_vector.assert_not_called()
    mock_retrieve.assert_called_once()


@patch("app.serving.onboarding_api.retrieve_candidates", return_value=[_CANDIDATE])
@patch("app.serving.onboarding_api.upsert_vector")
@patch("app.serving.onboarding_api.embed_query")
@patch("app.serving.onboarding_api.fetch_vector", return_value=[0.4, 0.5, 0.6])
@patch(
    "app.serving.onboarding_api._judge_checkpoint",
    return_value=CheckpointJudgment(show_candidates=True, ready_to_finish=True, interest_summary="plumbing focus"),
)
def test_checkpoint_skips_candidates_when_ready_to_finish(
    mock_judge, mock_fetch_vector, mock_embed_query, mock_upsert_vector, mock_retrieve, user
):
    """Even if the judge call returns show_candidates=True alongside
    ready_to_finish=True, no fresh candidate batch is retrieved -- there's no
    point previewing one right before handing the user off to their full
    feed. This is a code-level guard, not just prompt wording, since the
    judge call can't be trusted to always keep these mutually exclusive."""
    resp = client.post(
        "/onboarding/checkpoint",
        json={"messages": [{"role": "user", "content": "just the sink really"}]},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ready_to_finish"] is True
    assert data["candidates"] == []
    mock_embed_query.assert_not_called()
    mock_upsert_vector.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_streams_text_deltas(mock_get_client, user):
    """The streamed response body is the concatenation of delta chunks."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_stream(["Hi", " there", "!"])
    mock_get_client.return_value = mock_client

    resp = client.post(
        "/onboarding/chat", json={"messages": [{"role": "user", "content": "hello"}]}, headers=auth_header(user)
    )

    assert resp.status_code == 200
    assert resp.text == "Hi there!"


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_uses_base_prompt_unchanged_for_a_normal_turn(mock_get_client, user):
    """A normal turn (not ready_to_finish) gets the base system prompt as-is
    -- whether candidates are being shown this turn doesn't change the
    model's instructions at all (see OnboardingChatRequest's docstring:
    feeding candidate content here for the model to narrate was tried and
    found unreliable in real multi-turn conversations; the "this connects to
    what you said" signal now lives in a deterministic UI label instead)."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_stream(["ok"])
    mock_get_client.return_value = mock_client

    client.post(
        "/onboarding/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=auth_header(user)
    )

    _, kwargs = mock_client.responses.create.call_args
    assert kwargs["instructions"] == onboarding_api._CHAT_SYSTEM_PROMPT


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_ready_to_finish_uses_non_streamed_validated_reply(mock_get_client, user):
    """ready_to_finish skips the real token stream and uses the validated
    retry-until-compliant path instead (see _generate_finish_reply) -- a
    plain "?"-free reply on the first attempt should be returned as-is."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = SimpleNamespace(output_text="Your feed is ready, go check it out!")
    mock_get_client.return_value = mock_client

    resp = client.post(
        "/onboarding/chat",
        json={"messages": [{"role": "user", "content": "yes I liked those"}], "ready_to_finish": True},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    assert resp.text == "Your feed is ready, go check it out!"
    mock_client.responses.create.assert_called_once()
    assert mock_client.responses.create.call_args.kwargs.get("stream") is not True


@patch("app.serving.onboarding_api._get_client")
def test_onboarding_chat_ready_to_finish_retries_then_falls_back(mock_get_client, user):
    """If every retry still comes back with a question, fall back to the
    fixed closing line rather than surfacing a stray question on the turn
    that's supposed to wrap onboarding up."""
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        SimpleNamespace(output_text="How about eco-friendly gear?"),
        SimpleNamespace(output_text="What do you think of hiking tours?"),
        SimpleNamespace(output_text="Do you like camping too?"),
    ]
    mock_get_client.return_value = mock_client

    resp = client.post(
        "/onboarding/chat",
        json={"messages": [{"role": "user", "content": "yes I liked those"}], "ready_to_finish": True},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    assert resp.text == "Great chatting with you! Your personalized feed is ready -- go check it out!"
    assert mock_client.responses.create.call_count == 3
