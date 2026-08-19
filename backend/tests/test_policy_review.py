import itertools
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.campaigns.policy_review import _call_reviewer, _lookup_advertiser_history, review_campaign
from app.models import Campaign
from app.schemas import ReviewDecision


def _fake_function_call(name: str, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments="{}", call_id=call_id)


def _fake_response(output_parsed=None, output=None) -> SimpleNamespace:
    return SimpleNamespace(output_parsed=output_parsed, output=output or [])


def _mock_client(side_effect) -> MagicMock:
    """A fake AsyncOpenAI client whose responses.parse() yields from
    `side_effect` (a list, or an infinite itertools.repeat for the
    max-turns test) -- lets _call_reviewer's own loop logic be exercised
    deterministically without a real API call."""
    client = MagicMock()
    client.responses.parse = AsyncMock(side_effect=side_effect)
    return client


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._call_reviewer", new_callable=AsyncMock)
@patch("app.campaigns.policy_review.fetch_ad_policy", new_callable=AsyncMock, return_value="policy text")
async def test_review_campaign_approved(mock_fetch_policy, mock_call_reviewer):
    mock_call_reviewer.return_value = ReviewDecision(
        outcome="approved", reason="ordinary product ad, no policy conflicts", excluded_categories=[]
    )

    decision = await review_campaign(
        headline="Cordless Drill Kit",
        description="18V drill with two batteries",
        category="hardware",
        excluded_categories=[],
        user_id=1,
        campaign_id=1,
    )

    assert decision.outcome == "approved"
    mock_fetch_policy.assert_awaited_once()
    mock_call_reviewer.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._call_reviewer", new_callable=AsyncMock)
@patch("app.campaigns.policy_review.fetch_ad_policy", new_callable=AsyncMock, return_value="policy text")
async def test_review_campaign_adds_required_exclusions(mock_fetch_policy, mock_call_reviewer):
    mock_call_reviewer.return_value = ReviewDecision(
        outcome="approved",
        reason="alcohol campaign approved with required context exclusions added",
        excluded_categories=["sensitive", "health", "recovery"],
    )

    decision = await review_campaign(
        headline="Craft Beer Club",
        description="Monthly beer subscription",
        category="alcohol",
        excluded_categories=[],
        user_id=1,
        campaign_id=1,
    )

    assert decision.outcome == "approved"
    assert set(decision.excluded_categories) == {"sensitive", "health", "recovery"}


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._call_reviewer", new_callable=AsyncMock)
@patch("app.campaigns.policy_review.fetch_ad_policy", new_callable=AsyncMock, return_value="policy text")
async def test_review_campaign_needs_review(mock_fetch_policy, mock_call_reviewer):
    mock_call_reviewer.return_value = ReviewDecision(
        outcome="needs_review", reason="ambiguous financial claim, escalating", excluded_categories=[]
    )

    decision = await review_campaign(
        headline="Invest with us",
        description="Great returns for early investors",
        category="finance",
        excluded_categories=[],
        user_id=1,
        campaign_id=1,
    )

    assert decision.outcome == "needs_review"


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._call_reviewer", new_callable=AsyncMock)
@patch("app.campaigns.policy_review.fetch_ad_policy", new_callable=AsyncMock, return_value="policy text")
async def test_review_campaign_includes_research_notes_for_moderator(mock_fetch_policy, mock_call_reviewer):
    mock_call_reviewer.return_value = ReviewDecision(
        outcome="needs_review",
        reason="claim references a specific named product, could not confirm it exists",
        excluded_categories=[],
        research_notes="Searched for 'NovaCharge Battery Co' -- no independent lab results found.",
    )

    decision = await review_campaign(
        headline="NovaCharge Battery Co",
        description="Verified by independent lab testing, delivers 500 mile range",
        category="automotive",
        excluded_categories=[],
        user_id=1,
        campaign_id=1,
    )

    assert decision.outcome == "needs_review"
    assert decision.research_notes is not None
    assert "NovaCharge" in decision.research_notes


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._call_reviewer", new_callable=AsyncMock)
@patch("app.campaigns.policy_review.fetch_ad_policy", new_callable=AsyncMock, return_value="policy text")
async def test_review_campaign_research_notes_defaults_to_none(mock_fetch_policy, mock_call_reviewer):
    mock_call_reviewer.return_value = ReviewDecision(
        outcome="approved", reason="ordinary product ad, nothing to look up", excluded_categories=[]
    )

    decision = await review_campaign(
        headline="Cordless Drill Kit",
        description="18V drill with two batteries",
        category="hardware",
        excluded_categories=[],
        user_id=1,
        campaign_id=1,
    )

    assert decision.research_notes is None


def test_lookup_advertiser_history_summarizes_past_campaigns(db, advertiser_user):
    def _campaign(status: str, review_reason: str | None = None) -> Campaign:
        c = Campaign(
            user_id=advertiser_user.id,
            headline="pytest history headline",
            description="pytest history description",
            category="hardware",
            objective="conversions",
            budget_total=100.0,
            budget_spent=0.0,
            start_date=date(2020, 1, 1),
            end_date=date(2099, 1, 1),
            excluded_categories=[],
            status=status,
            review_reason=review_reason,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    active = _campaign("active")
    rejected1 = _campaign("rejected", review_reason="false health claim")
    rejected2 = _campaign("rejected", review_reason="missing required exclusions")
    under_review = _campaign("needs_review", review_reason="ambiguous financial claim")

    try:
        history = _lookup_advertiser_history(db, advertiser_user.id, exclude_campaign_id=-1)

        assert history["total_past_campaigns"] == 4
        assert history["by_status"] == {"active": 1, "rejected": 2, "needs_review": 1}
        assert set(history["recent_rejection_reasons"]) == {"false health claim", "missing required exclusions"}
    finally:
        for c in [active, rejected1, rejected2, under_review]:
            db.delete(c)
        db.commit()


def test_lookup_advertiser_history_excludes_the_campaign_under_review(db, advertiser_user, campaign):
    """The campaign currently being reviewed hasn't been decided yet -- it
    must not count as part of its own advertiser's history."""
    history = _lookup_advertiser_history(db, advertiser_user.id, exclude_campaign_id=campaign.id)
    assert history["total_past_campaigns"] == 0


# --------------------------------------------------------------------------
# _call_reviewer's own tool-calling loop -- mocked at the OpenAI client
# boundary so the loop's control flow (dispatch, feeding results back,
# terminating, the max-turns safety cap) is covered deterministically and
# cheaply, rather than only incidentally by the real-API adversarial tests.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._get_client")
async def test_call_reviewer_returns_immediately_when_no_tool_call(mock_get_client):
    decision = ReviewDecision(outcome="approved", reason="clean, ordinary ad", excluded_categories=[])
    mock_get_client.return_value = _mock_client([_fake_response(output_parsed=decision)])

    result = await _call_reviewer("policy text", "campaign text", user_id=1, campaign_id=1)

    assert result is decision
    assert mock_get_client.return_value.responses.parse.call_count == 1


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._lookup_advertiser_history")
@patch("app.campaigns.policy_review._get_client")
async def test_call_reviewer_executes_tool_and_feeds_result_back(mock_get_client, mock_lookup):
    lookup_result = {
        "total_past_campaigns": 2,
        "by_status": {"rejected": 2},
        "recent_rejection_reasons": ["unsubstantiated claim"],
    }
    mock_lookup.return_value = lookup_result
    decision = ReviewDecision(outcome="needs_review", reason="borderline, history is mixed", excluded_categories=[])
    call = _fake_function_call("lookup_advertiser_history", "call_abc")
    mock_get_client.return_value = _mock_client(
        [
            _fake_response(output_parsed=None, output=[call]),
            _fake_response(output_parsed=decision, output=[]),
        ]
    )

    result = await _call_reviewer("policy text", "campaign text", user_id=42, campaign_id=99)

    assert result is decision
    parse_mock = mock_get_client.return_value.responses.parse
    assert parse_mock.call_count == 2

    # user_id/exclude_campaign_id are passed through exactly, never taken
    # from anything the model supplied (the tool's own schema takes no
    # arguments -- call.arguments is never even read).
    (_db, called_user_id, called_exclude_id), _kwargs = mock_lookup.call_args
    assert called_user_id == 42
    assert called_exclude_id == 99

    second_call_input = parse_mock.call_args_list[1].kwargs["input"]
    function_outputs = [item for item in second_call_input if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert len(function_outputs) == 1
    assert function_outputs[0]["call_id"] == "call_abc"
    assert json.loads(function_outputs[0]["output"]) == lookup_result


@pytest.mark.asyncio
@patch("app.campaigns.policy_review._get_client")
async def test_call_reviewer_handles_unknown_tool_name_gracefully(mock_get_client):
    """A tool name the model hallucinates (or a future/renamed tool this
    code doesn't know about yet) must not crash the loop -- it gets a
    plain error result fed back, same as any other tool output, and the
    loop keeps going."""
    decision = ReviewDecision(outcome="approved", reason="clean", excluded_categories=[])
    call = _fake_function_call("some_tool_that_does_not_exist", "call_xyz")
    mock_get_client.return_value = _mock_client(
        [
            _fake_response(output_parsed=None, output=[call]),
            _fake_response(output_parsed=decision, output=[]),
        ]
    )

    result = await _call_reviewer("policy text", "campaign text", user_id=1, campaign_id=1)

    assert result is decision
    second_call_input = mock_get_client.return_value.responses.parse.call_args_list[1].kwargs["input"]
    function_outputs = [item for item in second_call_input if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert json.loads(function_outputs[0]["output"]) == {"error": "unknown tool 'some_tool_that_does_not_exist'"}


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)  # skip tenacity's real retry backoff
@patch("app.campaigns.policy_review._lookup_advertiser_history", return_value={})
@patch("app.campaigns.policy_review._get_client")
async def test_call_reviewer_gives_up_after_max_tool_turns(mock_get_client, mock_lookup, mock_sleep):
    """If the model just keeps calling tools and never returns a final
    decision, the loop must not run forever -- it has to give up with a
    clear error rather than hang the background review job indefinitely."""
    call = _fake_function_call("lookup_advertiser_history", "call_loop")
    always_calling = itertools.repeat(_fake_response(output_parsed=None, output=[call]))
    mock_get_client.return_value = _mock_client(always_calling)

    with pytest.raises(RuntimeError, match="did not return a final decision"):
        await _call_reviewer("policy text", "campaign text", user_id=1, campaign_id=1)

    # _call_reviewer is itself wrapped in @retry(stop_after_attempt(4)), so
    # the whole loop (4 tool-turns each) reruns up to 4 times before the
    # RuntimeError is finally allowed through -- assert the loop's own cap
    # held on every attempt rather than pinning an exact total.
    call_count = mock_get_client.return_value.responses.parse.call_count
    assert call_count % 4 == 0
    assert call_count >= 4
