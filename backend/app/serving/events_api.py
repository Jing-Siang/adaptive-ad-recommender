from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.logging_utils import log_event
from app.models import Campaign, Event
from app.schemas import CurrentUser, ImpressionRequest, ReactionClearRequest, ReactionRequest, ReportRequest
from app.serving.feedback import clear_feedback, record_feedback

router = APIRouter(prefix="/events", tags=["events"])

# Reports on a campaign crossing this count auto-flip it to needs_review.
# Deliberately a flat number for this phase -- see docs/future_ideas.md for
# the richer escalation-agent version this is expected to be replaced by.
REPORT_THRESHOLD = 3


@router.post("/impression", status_code=201)
def log_impression(
    request: ImpressionRequest, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)
) -> dict:
    """Fired client-side (Intersection Observer) when a feed item actually
    scrolls into view. Pure DB insert -- no profile nudge, no budget debit."""
    db.add(Event(user_id=str(current.id), campaign_id=int(request.ad_id), event_type="impression"))
    db.commit()
    return {"status": "recorded"}


@router.post("/reaction", status_code=201)
def react(
    request: ReactionRequest, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)
) -> dict:
    """like/dislike/interested: logs the event and nudges the user's profile
    vector; like/interested also debit the campaign's budget."""
    user_id = str(current.id)
    try:
        record_feedback(db, user_id, request.ad_id, request.reaction)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    db.add(Event(user_id=user_id, campaign_id=int(request.ad_id), event_type=request.reaction))
    db.commit()
    log_event("reaction_recorded", user_id=user_id, ad_id=request.ad_id, reaction=request.reaction)
    return {"status": "recorded"}


@router.delete("/reaction", status_code=200)
def unreact(
    request: ReactionClearRequest, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)
) -> dict:
    """Removes the user's current reaction to an ad entirely, reversing
    whatever nudge/debit it applied -- the symmetric inverse of react().
    No Event row on removal: Event is an append-only record of what
    happened (the original reaction genuinely did occur), not current
    state -- that's exactly what the reactions table is for."""
    user_id = str(current.id)
    try:
        removed = clear_feedback(db, user_id, request.ad_id) is not None
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if removed:
        log_event("reaction_removed", user_id=user_id, ad_id=request.ad_id)
    return {"status": "removed" if removed else "no_reaction"}


@router.post("/report", status_code=201)
def report(
    request: ReportRequest, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)
) -> dict:
    """Policy complaint -- logs the event, then checks whether this campaign
    has crossed REPORT_THRESHOLD total reports and auto-flips it to
    needs_review if so (counted straight from the event log, no separate
    counter column to keep in sync)."""
    user_id = str(current.id)
    campaign_id = int(request.ad_id)
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"campaign '{campaign_id}' not found")

    db.add(
        Event(
            user_id=user_id,
            campaign_id=campaign_id,
            event_type="report",
            report_category=request.category,
            report_reason=request.reason,
        )
    )
    db.commit()

    report_count = (
        db.query(func.count(Event.id))
        .filter(Event.campaign_id == campaign_id, Event.event_type == "report")
        .scalar()
    )
    if report_count >= REPORT_THRESHOLD and campaign.status == "active":
        campaign.status = "needs_review"
        db.commit()
        log_event("campaign_auto_flagged", campaign_id=campaign_id, report_count=report_count)

    log_event("report_recorded", user_id=user_id, ad_id=request.ad_id, category=request.category)
    return {"status": "recorded"}
