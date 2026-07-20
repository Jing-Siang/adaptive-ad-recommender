from datetime import date, datetime

from sqlalchemy import ARRAY, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

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
