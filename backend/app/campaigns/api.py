from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_role
from app.core.db import get_db
from app.campaigns.review_jobs import review_campaign_job
from app.core.logging_utils import log_event
from app.models import Campaign, User
from app.core.queue import campaign_review_queue
from app.schemas import (
    CampaignCreateRequest,
    CampaignListResponse,
    CampaignResponse,
    CurrentUser,
    ModerationRequest,
)

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


_SORTABLE_COLUMNS = {
    "created_at": Campaign.created_at,
    "headline": Campaign.headline,
    "budget_total": Campaign.budget_total,
}


@router.get("", response_model=CampaignListResponse)
def list_campaigns(
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    review_reason_search: str | None = None,
    sort_by: Literal["created_at", "headline", "budget_total"] = "created_at",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CampaignListResponse:
    """List campaigns, paginated, optionally filtered by status —
    status=needs_review is the moderator queue — and/or category, and/or
    searched by headline and/or review_reason (case-insensitive substring,
    independent of each other), sorted by any of _SORTABLE_COLUMNS.
    Filtering/searching/sorting/paging all happen in the query itself, not
    after loading rows into Python, since the catalog is thousands of
    rows."""
    query = db.query(Campaign)
    if status:
        query = query.filter_by(status=status)
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Campaign.headline.ilike(f"%{search}%"))
    if review_reason_search:
        query = query.filter(Campaign.review_reason.ilike(f"%{review_reason_search}%"))

    total = query.count()
    total_pages = max(1, -(-total // page_size))  # ceil division
    column = _SORTABLE_COLUMNS[sort_by]
    order = column.asc() if sort_dir == "asc" else column.desc()
    items = query.order_by(order).offset((page - 1) * page_size).limit(page_size).all()
    return CampaignListResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("/{campaign_id}/moderate", response_model=CampaignResponse)
def moderate_campaign(
    campaign_id: int,
    request: ModerationRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_role("moderator")),
) -> Campaign:
    """Human moderator resolves a needs_review campaign. Requires the
    moderator role (see docs/auth_plan.md). reviewed_by is derived from the
    authenticated moderator's own account (display_name), not a
    caller-supplied field -- now that real accounts exist, a freeform name
    added nothing a verified account doesn't already give more reliably.
    Looked up fresh from Postgres rather than trusting a JWT claim, since
    get_current_user's token has no display_name in it and a display name
    can change without a fresh login. Embedding/indexing into Pinecone
    happens asynchronously via pinecone_sync_consumer.py, not here (see
    docs/kafka_cdc_plan.md)."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.status != "needs_review":
        raise HTTPException(status_code=409, detail=f"campaign is '{campaign.status}', not awaiting review")

    reviewer = db.get(User, current.id)
    reviewed_by = reviewer.display_name

    campaign.status = "active" if request.outcome == "approved" else "rejected"
    campaign.review_reason = request.reason
    campaign.reviewed_by = reviewed_by
    campaign.reviewed_at = datetime.now(UTC)
    db.commit()
    db.refresh(campaign)

    log_event(
        "campaign_moderated",
        campaign_id=campaign_id,
        outcome=request.outcome,
        reviewed_by=reviewed_by,
    )

    return campaign
