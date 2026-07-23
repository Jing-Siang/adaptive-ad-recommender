"""Pydantic schemas shared across the app. Kept in one file (not split into
app/serving/ and app/campaigns/) because a handful of schemas here — Ad,
AdCandidate — are used by both sides: campaigns get embedded into Pinecone
using the Ad shape, and the serving pipeline reads them back out the same way.

Organized into two sections matching the two pipelines in app/:
  - Serving:   retrieve -> re-rank -> guardrail -> serve -> feedback
  - Campaigns: advertiser submits -> policy review -> (maybe) moderation
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Serving: recommending an ad to a user (app/serving/)
# --------------------------------------------------------------------------


class Ad(BaseModel):
    """A single ad's content — what actually gets embedded into Pinecone.
    Filled in from an approved Campaign's headline/description/category
    (see app/serving/retrieval.py:index_campaign)."""

    ad_id: str
    headline: str
    description: str
    category: str
    price: float | None = None


class AdCandidate(Ad):
    """An Ad plus how close it scored to a user's profile vector — the
    result of the cheap vector-search pass in retrieve_candidates, before
    any LLM re-ranking happens."""

    similarity_score: float


class UserCreateRequest(BaseModel):
    """Request body for POST /users. Seeds a user's starting profile vector
    from a free-text interest summary -- called once, at the first
    onboarding checkpoint (see app/serving/users.py). No accounts/auth:
    user_id is just a caller-supplied string, same attribution-not-
    authentication pattern as advertiser_name."""

    user_id: str
    interest_summary: str


class UserResponse(BaseModel):
    """Read model for a user's profile -- returned by both POST /users and
    GET /users/{user_id}. Deliberately excludes the raw profile_vector
    (an internal Pinecone implementation detail, not meaningful for
    display or worth the payload size)."""

    user_id: str
    interest_summary: str


class RankedAd(BaseModel):
    """One candidate's LLM-assigned relevance, after re-ranking. This is
    the structured output ranking.py asks the LLM for — one per candidate."""

    ad_id: str
    relevance_score: float = Field(ge=0, le=1)
    justification: str


class RankingResponse(BaseModel):
    """The full structured-output shape the LLM returns in one call: a
    ranking for every candidate it was given."""

    rankings: list[RankedAd]


class GuardrailResult(BaseModel):
    """Whether a specific ad is allowed to serve in the current context —
    the outcome of guardrails.check_guardrails, run after re-ranking."""

    ad_id: str
    allowed: bool
    reason: str | None = None


class RecommendationRequest(BaseModel):
    """Request body for POST /recommend."""

    user_id: str
    top_k: int = 10


class RecommendationTrace(BaseModel):
    """Full decision trace returned by POST /recommend — every candidate,
    every ranking, every guardrail check, and which ad (if any) actually
    got served. This is the "explainability" artifact: enough to answer
    "why was this ad chosen" after the fact."""

    user_id: str
    candidates: list[AdCandidate]
    rankings: list[RankedAd]
    guardrail_results: list[GuardrailResult]
    served_ad_id: str | None


class FeedbackEvent(BaseModel):
    """Request body for POST /feedback — a simulated click/no_click/
    conversion outcome for a served ad, used to nudge the user's profile
    vector (see feedback.update_profile_vector)."""

    user_id: str
    ad_id: str
    outcome: str = Field(pattern="^(click|no_click|conversion)$")


# --------------------------------------------------------------------------
# Campaigns: an advertiser submitting a campaign for review (app/campaigns/)
# --------------------------------------------------------------------------


class ReviewDecision(BaseModel):
    """Structured output the policy review agent returns (see
    campaigns/policy_review.py). Drives what happens next: approved ->
    embed + index into Pinecone; rejected -> reason stored, visible to the
    submitter; needs_review -> surfaces in the moderator queue."""

    outcome: Literal["approved", "rejected", "needs_review"]
    reason: str
    excluded_categories: list[str] = Field(
        default_factory=list,
        description="Final excluded_categories for the campaign — include any "
        "policy-required exclusions the submission was missing.",
    )
    research_notes: str | None = Field(
        default=None,
        description="Web search findings relevant to the decision (e.g. verifying a "
        "health/financial claim) -- for a human moderator's benefit on needs_review "
        "cases, not something the outcome itself should be based solely on.",
    )


class CampaignCreateRequest(BaseModel):
    """Request body for POST /campaigns. advertiser_name is get-or-create —
    there's no separate advertiser-signup step (no auth in this project,
    see docs/spec.md)."""

    advertiser_name: str
    headline: str
    description: str
    category: str
    objective: str
    budget_total: float = Field(gt=0)
    start_date: date
    end_date: date
    excluded_categories: list[str] = Field(default_factory=list)


class CampaignResponse(BaseModel):
    """Read model for a Campaign row — returned by every campaigns/api.py
    endpoint. model_config lets this be built directly from a SQLAlchemy
    Campaign instance (Pydantic v2's from_attributes, formerly orm_mode)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    headline: str
    description: str
    category: str
    objective: str
    budget_total: float
    budget_spent: float
    start_date: date
    end_date: date
    excluded_categories: list[str]
    status: str
    review_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ModerationRequest(BaseModel):
    """Request body for POST /campaigns/{id}/moderate — a human resolving a
    needs_review campaign. reviewed_by is a freeform name for the audit
    trail only, not a verified identity (no auth in this project)."""

    outcome: Literal["approved", "rejected"]
    reason: str
    reviewed_by: str
