from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_role
from app.core.db import get_db
from app.campaigns.review_jobs import review_campaign_job
from app.core.logging_utils import log_event
from app.models import Campaign
from app.core.queue import campaign_review_queue
from app.schemas import CampaignCreateRequest, CampaignResponse, CurrentUser, ModerationRequest

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignResponse, status_code=201)
def create_campaign(
    request: CampaignCreateRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_role("advertiser", "moderator")),
) -> Campaign:
    """Submit a new campaign. Returns immediately with status=pending_review —
    the policy review agent runs asynchronously via the campaign_review queue.
    The submitting account is the owner directly (user_id) -- no separate
    Advertiser entity, see docs/auth_plan.md."""
    campaign = Campaign(
        user_id=current.id,
        headline=request.headline,
        description=request.description,
        category=request.category,
        objective=request.objective,
        budget_total=request.budget_total,
        budget_spent=0.0,
        start_date=request.start_date,
        end_date=request.end_date,
        excluded_categories=request.excluded_categories,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    campaign_review_queue.enqueue(review_campaign_job, campaign.id)
    log_event("campaign_submitted", campaign_id=campaign.id, user_id=current.id)

    return campaign


@router.get("", response_model=list[CampaignResponse])
def list_campaigns(status: str | None = None, db: Session = Depends(get_db)) -> list[Campaign]:
    """List campaigns, optionally filtered by status — status=needs_review is the
    moderator queue."""
    query = db.query(Campaign)
    if status:
        query = query.filter_by(status=status)
    return query.order_by(Campaign.created_at.desc()).all()


@router.post("/{campaign_id}/moderate", response_model=CampaignResponse)
def moderate_campaign(
    campaign_id: int,
    request: ModerationRequest,
    db: Session = Depends(get_db),
    _current: CurrentUser = Depends(require_role("moderator")),
) -> Campaign:
    """Human moderator resolves a needs_review campaign. Requires the
    moderator role (see docs/auth_plan.md) -- reviewed_by is still a
    freeform name, not yet derived from the authenticated account, since
    that's a separate change from just closing the access-control gap.
    Embedding/indexing into Pinecone happens asynchronously via
    pinecone_sync_consumer.py, not here (see docs/kafka_cdc_plan.md)."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.status != "needs_review":
        raise HTTPException(status_code=409, detail=f"campaign is '{campaign.status}', not awaiting review")

    campaign.status = "active" if request.outcome == "approved" else "rejected"
    campaign.review_reason = request.reason
    campaign.reviewed_by = request.reviewed_by
    campaign.reviewed_at = datetime.now(UTC)
    db.commit()
    db.refresh(campaign)

    log_event(
        "campaign_moderated",
        campaign_id=campaign_id,
        outcome=request.outcome,
        reviewed_by=request.reviewed_by,
    )

    return campaign
