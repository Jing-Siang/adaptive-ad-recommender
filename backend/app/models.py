from datetime import date, datetime

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Index, String, UniqueConstraint, func
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

# Valid values for User.role, same "Pydantic-layer enum, not DB enum" pattern
# as above. A superset relationship, not a multi-role table: moderator can do
# everything advertiser/end_user can, advertiser everything end_user can.
USER_ROLES = (
    "end_user",    # default on first login -- onboarding, feed, reactions
    "advertiser",  # + submit campaigns, view performance dashboard
    "moderator",   # + approve/reject campaigns
)


class User(Base):
    """A real, authenticated account (Google OAuth) -- see docs/auth_plan.md.
    id is what everything downstream calls user_id: Pinecone vector IDs
    (namespace="users"), Reaction.user_id, Event.user_id -- stringified,
    same pattern already used for Campaign.id as Pinecone's ad vector ID."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500), default=None)
    role: Mapped[str] = mapped_column(String(20), default="end_user")
    # True only once the user actually finishes onboarding (clicks "Continue
    # to your feed") -- NOT the same as "has a profile vector in Pinecone",
    # which gets seeded much earlier, on the first onboarding_checkpoint
    # round that decides to show candidates. Using vector-existence for this
    # would route a mid-conversation reload straight to the feed instead of
    # back into onboarding (see docs/auth_plan.md).
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The submitting account -- a User with role="advertiser" (or
    # "moderator", which is a superset). No separate Advertiser entity:
    # the "advertiser name" a campaign is attributed to is just that
    # account's User.display_name, not a distinct business-name field
    # (see docs/auth_plan.md).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    # Creative
    # index=True on headline/category/status/budget_total/budget_spent/
    # created_at -- GET /campaigns and GET /performance/campaigns filter by
    # category/status and sort by any of these, on a catalog now in the
    # thousands of rows. The index on headline only serves ORDER BY, not
    # the search box's leading-wildcard ILIKE '%term%' -- a plain btree
    # index can't serve that (would need a pg_trgm GIN index instead).
    headline: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(100), index=True)

    # Campaign terms
    objective: Mapped[str] = mapped_column(String(100))
    budget_total: Mapped[float] = mapped_column(Float, index=True)
    # budget_spent, not budget_total, is what GET /performance/campaigns
    # sorts by (sort_by="spend") -- a distinct column from the Campaigns
    # page's "Budget" sort, needs its own index.
    budget_spent: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    start_date: Mapped[date]
    end_date: Mapped[date]
    excluded_categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Review outcome — kept on this same row rather than a separate table
    status: Mapped[str] = mapped_column(String(20), default="pending_review", index=True)
    review_reason: Mapped[str | None] = mapped_column(String(1000), default=None)
    research_notes: Mapped[str | None] = mapped_column(String(2000), default=None)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


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


class BlocklistEntry(Base):
    """Permanent per-user "do not show this ad again" exclusion -- not a
    learning signal (no profile-vector nudge, no Event row, see
    serving/users.py's do_not_show), just a row checked in
    retrieve_candidates. Previously lived in Pinecone metadata as a JSON
    array on the user's profile vector, which meant every add was a
    fetch-then-write round trip (measured ~3s: a full metadata read, plus
    the write). A plain table makes an add a single INSERT with no
    read-before-write."""

    __tablename__ = "blocklist_entries"
    __table_args__ = (UniqueConstraint("user_id", "campaign_id", name="uq_blocklist_user_campaign"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reaction(Base):
    """The user's *current* reaction to an ad -- one row per (user_id,
    campaign_id), updated in place on switch/re-click. Separate from Event
    (the append-only history the dashboard counts) on purpose: this table
    answers "what does this user currently think of this ad", which is a
    current-state question, not a log query. user_id is a real FK to users
    now that accounts exist (see docs/auth_plan.md) -- this table's shape
    was deliberately built to only need that one change."""

    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("user_id", "campaign_id", name="uq_reactions_user_campaign"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    reaction: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    campaign: Mapped["Campaign"] = relationship()
