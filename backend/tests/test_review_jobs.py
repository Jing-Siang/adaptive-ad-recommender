from unittest.mock import AsyncMock, patch

from app.campaigns.review_jobs import review_campaign_job
from app.models import Campaign
from app.schemas import ReviewDecision


@patch("app.campaigns.review_jobs.index_campaign")
@patch("app.campaigns.review_jobs.review_campaign", new_callable=AsyncMock)
def test_review_campaign_job_approved_indexes_campaign(mock_review, mock_index, db, campaign):
    campaign.status = "pending_review"
    db.commit()
    mock_review.return_value = ReviewDecision(
        outcome="approved", reason="looks fine", excluded_categories=[], research_notes="found no conflicting claims"
    )

    review_campaign_job(campaign.id)

    db.refresh(campaign)
    assert campaign.status == "active"
    assert campaign.review_reason == "looks fine"
    assert campaign.research_notes == "found no conflicting claims"
    assert campaign.reviewed_by == "ai_policy_agent"
    assert campaign.reviewed_at is not None
    mock_index.assert_called_once()
    assert mock_index.call_args[0][0].id == campaign.id


@patch("app.campaigns.review_jobs.index_campaign")
@patch("app.campaigns.review_jobs.review_campaign", new_callable=AsyncMock)
def test_review_campaign_job_rejected_does_not_index(mock_review, mock_index, db, campaign):
    campaign.status = "pending_review"
    db.commit()
    mock_review.return_value = ReviewDecision(outcome="rejected", reason="prohibited claim", excluded_categories=[])

    review_campaign_job(campaign.id)

    db.refresh(campaign)
    assert campaign.status == "rejected"
    assert campaign.review_reason == "prohibited claim"
    assert campaign.research_notes is None
    mock_index.assert_not_called()


@patch("app.campaigns.review_jobs.index_campaign")
@patch("app.campaigns.review_jobs.review_campaign", new_callable=AsyncMock)
def test_review_campaign_job_needs_review_does_not_index(mock_review, mock_index, db, campaign):
    campaign.status = "pending_review"
    db.commit()
    mock_review.return_value = ReviewDecision(outcome="needs_review", reason="ambiguous", excluded_categories=[])

    review_campaign_job(campaign.id)

    db.refresh(campaign)
    assert campaign.status == "needs_review"
    mock_index.assert_not_called()


@patch("app.campaigns.review_jobs.index_campaign")
@patch("app.campaigns.review_jobs.review_campaign", new_callable=AsyncMock)
def test_review_campaign_job_applies_required_exclusions(mock_review, mock_index, db, campaign):
    campaign.status = "pending_review"
    campaign.category = "alcohol"
    campaign.excluded_categories = []
    db.commit()
    mock_review.return_value = ReviewDecision(
        outcome="approved", reason="added required exclusions", excluded_categories=["sensitive", "health"]
    )

    review_campaign_job(campaign.id)

    db.refresh(campaign)
    assert campaign.excluded_categories == ["sensitive", "health"]


@patch("app.campaigns.review_jobs.index_campaign")
@patch("app.campaigns.review_jobs.review_campaign", new_callable=AsyncMock)
def test_review_campaign_job_missing_campaign_does_not_crash(mock_review, mock_index):
    review_campaign_job(999_999_999)

    mock_review.assert_not_called()
    mock_index.assert_not_called()
