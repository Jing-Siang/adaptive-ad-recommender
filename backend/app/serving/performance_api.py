from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

from app.core.auth import require_role
from app.core.db import get_db
from app.models import Campaign, Event
from app.schemas import CampaignPerformance, CurrentUser, PerformanceResponse, PerformanceTotals, PerformanceTrendPoint

router = APIRouter(tags=["performance"])


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@router.get("/performance", response_model=PerformanceResponse)
def get_performance(
    db: Session = Depends(get_db),
    _current: CurrentUser = Depends(require_role("advertiser", "moderator")),
) -> PerformanceResponse:
    """Aggregate CTR/engagement/dislike-rate/spend/CPA across all activity,
    plus a daily trend line and a per-campaign breakdown -- everything reads
    straight from the events table (the real history) and Campaign.budget_spent
    (already the source of truth for spend, no need to re-derive it)."""
    totals_by_type: dict[str, int] = dict(db.query(Event.event_type, func.count(Event.id)).group_by(Event.event_type))
    impressions = totals_by_type.get("impression", 0)
    likes = totals_by_type.get("like", 0)
    dislikes = totals_by_type.get("dislike", 0)
    conversions = totals_by_type.get("interested", 0)
    reports = totals_by_type.get("report", 0)
    total_spend = db.query(func.coalesce(func.sum(Campaign.budget_spent), 0.0)).scalar()

    totals = PerformanceTotals(
        impressions=impressions,
        likes=likes,
        dislikes=dislikes,
        conversions=conversions,
        reports=reports,
        ctr=_rate(conversions, impressions),
        engagement_rate=_rate(likes, impressions),
        dislike_rate=_rate(dislikes, impressions),
        total_spend=total_spend,
        avg_cpa=(total_spend / conversions) if conversions else None,
    )

    day_expr = cast(Event.created_at, Date)
    trend_rows = (
        db.query(day_expr.label("day"), Event.event_type, func.count(Event.id))
        .group_by("day", Event.event_type)
        .order_by("day")
        .all()
    )
    trend_by_day: dict = defaultdict(lambda: {"impression": 0, "interested": 0})
    for day, event_type, count in trend_rows:
        if event_type in ("impression", "interested"):
            trend_by_day[day][event_type] = count
    trend = [
        PerformanceTrendPoint(
            date=day,
            impressions=counts["impression"],
            conversions=counts["interested"],
            ctr=_rate(counts["interested"], counts["impression"]),
        )
        for day, counts in sorted(trend_by_day.items())
    ]

    campaign_event_rows = db.query(Event.campaign_id, Event.event_type, func.count(Event.id)).group_by(
        Event.campaign_id, Event.event_type
    )
    counts_by_campaign: dict = defaultdict(lambda: defaultdict(int))
    for campaign_id, event_type, count in campaign_event_rows:
        counts_by_campaign[campaign_id][event_type] = count

    campaigns = []
    for c in db.query(Campaign).order_by(Campaign.id).all():
        counts = counts_by_campaign.get(c.id, {})
        c_impressions = counts.get("impression", 0)
        c_conversions = counts.get("interested", 0)
        campaigns.append(
            CampaignPerformance(
                campaign_id=c.id,
                headline=c.headline,
                status=c.status,
                impressions=c_impressions,
                likes=counts.get("like", 0),
                dislikes=counts.get("dislike", 0),
                conversions=c_conversions,
                reports=counts.get("report", 0),
                ctr=_rate(c_conversions, c_impressions),
                spend=c.budget_spent,
            )
        )

    return PerformanceResponse(totals=totals, trend=trend, campaigns=campaigns)
