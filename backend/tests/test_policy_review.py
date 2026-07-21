from unittest.mock import AsyncMock, patch

import pytest

from app.campaigns.policy_review import review_campaign
from app.schemas import ReviewDecision


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
    )

    assert decision.outcome == "needs_review"
