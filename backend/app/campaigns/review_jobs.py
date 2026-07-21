import asyncio
from datetime import UTC, datetime

from app.core.db import SessionLocal
from app.core.logging_utils import log_event
from app.models import Campaign
from app.campaigns.policy_review import review_campaign
from app.campaigns.indexing import index_campaign

REVIEWED_BY_AGENT = "ai_policy_agent"

# Maps a ReviewDecision.outcome to the Campaign.status it results in.
_STATUS_FOR_OUTCOME = {
    "approved": "active",
    "rejected": "rejected",
    "needs_review": "needs_review",
}


def review_campaign_job(campaign_id: int) -> None:
    """RQ job: run the policy review agent against a campaign and persist the
    outcome. Approved campaigns are embedded and indexed into Pinecone."""
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            log_event("campaign_review_job_missing", campaign_id=campaign_id)
            return

        decision = asyncio.run(
            review_campaign(
                headline=campaign.headline,
                description=campaign.description,
                category=campaign.category,
                excluded_categories=campaign.excluded_categories,
            )
        )

        campaign.status = _STATUS_FOR_OUTCOME[decision.outcome]
        campaign.review_reason = decision.reason
        campaign.excluded_categories = decision.excluded_categories or campaign.excluded_categories
        campaign.reviewed_by = REVIEWED_BY_AGENT
        campaign.reviewed_at = datetime.now(UTC)
        db.commit()

        log_event(
            "campaign_reviewed",
            campaign_id=campaign_id,
            outcome=decision.outcome,
            status=campaign.status,
            reviewed_by=REVIEWED_BY_AGENT,
        )

        if decision.outcome == "approved":
            index_campaign(campaign)
            log_event("campaign_indexed", campaign_id=campaign_id)
    finally:
        db.close()
