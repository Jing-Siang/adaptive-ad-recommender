from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.campaigns.policy_review import _lookup_advertiser_history, review_campaign
from app.models import Campaign
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
