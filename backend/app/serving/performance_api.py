from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, Float, cast, case, func
from sqlalchemy.orm import Session

from app.core.auth import require_role
from app.core.db import get_db
from app.models import Campaign, Event
from app.schemas import (
    CampaignPerformance,
    CampaignPerformanceListResponse,
    CurrentUser,
    PerformanceResponse,
    PerformanceTotals,
    PerformanceTrendPoint,
)

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

    return PerformanceResponse(totals=totals, trend=trend)


def _campaign_performance_columns():
    """Conditional-aggregate expressions (one Event scan, pivoted into
    per-type counts) reused for both the SELECT list and ORDER BY -- lets
    the per-campaign breakdown be sorted/paginated in SQL instead of
    loading every campaign's full event history into Python."""
    impressions = func.sum(case((Event.event_type == "impression", 1), else_=0))
    likes = func.sum(case((Event.event_type == "like", 1), else_=0))
    dislikes = func.sum(case((Event.event_type == "dislike", 1), else_=0))
    conversions = func.sum(case((Event.event_type == "interested", 1), else_=0))
    reports = func.sum(case((Event.event_type == "report", 1), else_=0))
    ctr = func.coalesce(cast(conversions, Float) / func.nullif(impressions, 0), 0.0)
    return {
        "headline": Campaign.headline,
        "impressions": impressions,
        "likes": likes,
        "dislikes": dislikes,
        "conversions": conversions,
        "reports": reports,
        "ctr": ctr,
        "spend": Campaign.budget_spent,
    }


@router.get("/performance/campaigns", response_model=CampaignPerformanceListResponse)
def get_performance_campaigns(
    status: str | None = None,
    search: str | None = None,
    sort_by: Literal[
        "headline", "impressions", "likes", "dislikes", "conversions", "reports", "ctr", "spend"
    ] = "impressions",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _current: CurrentUser = Depends(require_role("advertiser", "moderator")),
) -> CampaignPerformanceListResponse:
    """Paginated per-campaign performance breakdown -- filtering/searching/
    sorting/paging all happen in the query itself (a single outer-joined,
    grouped aggregate), not after loading every campaign's event history
    into Python, since the catalog is thousands of rows. A LEFT JOIN (not
    INNER) so a campaign with zero events still appears, with all-zero
    counts, rather than being silently omitted."""
    columns = _campaign_performance_columns()
    query = (
        db.query(
            Campaign.id.label("campaign_id"),
            Campaign.headline.label("headline"),
            Campaign.status.label("status"),
            Campaign.budget_spent.label("spend"),
            columns["impressions"].label("impressions"),
            columns["likes"].label("likes"),
            columns["dislikes"].label("dislikes"),
            columns["conversions"].label("conversions"),
            columns["reports"].label("reports"),
            columns["ctr"].label("ctr"),
        )
        .outerjoin(Event, Event.campaign_id == Campaign.id)
        .group_by(Campaign.id, Campaign.headline, Campaign.status, Campaign.budget_spent)
    )
    if status:
        query = query.filter(Campaign.status == status)
    if search:
        query = query.filter(Campaign.headline.ilike(f"%{search}%"))

    total = query.count()
    total_pages = max(1, -(-total // page_size))  # ceil division
    order_column = columns[sort_by]
    order = order_column.asc() if sort_dir == "asc" else order_column.desc()
    rows = query.order_by(order).offset((page - 1) * page_size).limit(page_size).all()

    items = [
        CampaignPerformance(
            campaign_id=row.campaign_id,
            headline=row.headline,
            status=row.status,
            impressions=row.impressions,
            likes=row.likes,
            dislikes=row.dislikes,
            conversions=row.conversions,
            reports=row.reports,
            ctr=row.ctr,
            spend=row.spend,
        )
        for row in rows
    ]
    return CampaignPerformanceListResponse(
        items=items, total=total, page=page, page_size=page_size, total_pages=total_pages
    )
