from datetime import date, datetime

from sqlalchemy import ARRAY, DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Valid values for Event.event_type -- same "Pydantic-layer enum, not a DB
# enum" pattern as CAMPAIGN_STATUSES below, so adding a new event type
# doesn't require a migration.
EVENT_TYPES = (
    "impression",  # ad scrolled into view (Intersection Observer, client-triggered)
    "like",        # mild positive signal
    "dislike",     # explicit negative signal
    "interested",  # strong positive signal -- this is "conversion" for CTR/CPA purposes
    "report",      # policy complaint; report_category/report_reason are set
)

# Valid values for Campaign.status, enforced at the Pydantic layer (see schemas.py),
# not as a DB-level enum, so adding a new status doesn't require a migration.
CAMPAIGN_STATUSES = (
    "pending_review",  # just submitted, review job queued/running
    "needs_review",    # AI reviewer wasn't confident, waiting on a human moderator
    "rejected",        # AI or human rejected it
    "active",          # approved and eligible for serving (subject to budget/dates)
    "completed",       # budget exhausted or end_date passed
)


class Advertiser(Base):
    __tablename__ = "advertisers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="advertiser")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"))

    # Creative
    headline: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(100))

    # Campaign terms
    objective: Mapped[str] = mapped_column(String(100))
    budget_total: Mapped[float] = mapped_column(Float)
    budget_spent: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[date]
    end_date: Mapped[date]
    excluded_categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Review outcome — kept on this same row rather than a separate table
    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    review_reason: Mapped[str | None] = mapped_column(String(1000), default=None)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    advertiser: Mapped["Advertiser"] = relationship(back_populates="campaigns")


class Event(Base):
    """One row per impression/reaction/report -- the full history the
    performance dashboard aggregates over. record_feedback's profile-vector
    nudge only ever kept the *last* outcome, so this table is the actual
    event log that was missing for charting trends."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_campaign_id_created_at", "campaign_id", "created_at"),
        Index("ix_events_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200))
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    event_type: Mapped[str] = mapped_column(String(20))
    report_category: Mapped[str | None] = mapped_column(String(50), default=None)
    report_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign: Mapped["Campaign"] = relationship()
