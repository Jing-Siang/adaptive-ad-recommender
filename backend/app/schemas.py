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

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------
# Auth: Google OAuth login, our own JWT session (see docs/auth_plan.md)
# --------------------------------------------------------------------------


class CurrentUser(BaseModel):
    """Decoded straight from a verified access token's claims -- no DB hit
    per request, the whole point of a stateless access token. Only carries
    what's actually in the token; see AccountResponse for the full record."""

    id: int
    email: str
    role: str


class GoogleLoginRequest(BaseModel):
    """Request body for POST /auth/google -- the ID token Google's Identity
    Services JS handed the frontend directly, verified server-side against
    Google's public keys before we trust any of its claims."""

    id_token: str


class AccountResponse(BaseModel):
    """Read model for a User account -- returned by /auth/google, /auth/me.
    Distinct from UserResponse (the Pinecone interest-profile read model,
    a different concept -- this is the real, authenticated identity)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    avatar_url: str | None
    role: str
    onboarding_completed: bool


class AuthTokenResponse(BaseModel):
    """Response for /auth/google and /auth/refresh -- the access token goes
    in the body (frontend stores it in memory/localStorage); the refresh
    token is never in a JSON body at all, only set as an httpOnly cookie
    directly on the response."""

    access_token: str
    user: AccountResponse


# --------------------------------------------------------------------------
# Serving: recommending an ad to a user (app/serving/)
# --------------------------------------------------------------------------


class Ad(BaseModel):
    """A single ad's content — what actually gets embedded into Pinecone.
    Filled in from an approved Campaign's headline/description/category
    (see app/campaigns/pinecone_sync_consumer.py)."""

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
    """Request body for POST /users/me. Seeds the caller's starting profile
    vector from a free-text interest summary -- called once, at the first
    onboarding checkpoint (see app/serving/users.py). user_id comes from the
    authenticated account (see docs/auth_plan.md), not a request field."""

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
    """Request body for POST /recommend. user_id comes from the
    authenticated account, not a request field (see docs/auth_plan.md)."""

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


class FeedItem(AdCandidate):
    """One ad in a batch-recommend response: an AdCandidate (content +
    similarity_score) plus its LLM re-rank result -- enough to render the
    feed card and answer "why am I seeing this" with no separate call."""

    relevance_score: float = Field(ge=0, le=1)
    justification: str


class BatchRecommendationRequest(BaseModel):
    """Request body for POST /recommend/batch -- the feed-facing recommend
    call. One embed, one Pinecone query, one LLM re-rank covering the whole
    batch, one guardrail pass; contrast with RecommendationRequest, which is
    single-item. batch_size is capped to keep a single re-rank call's cost
    bounded. user_id comes from the authenticated account, not a request
    field (see docs/auth_plan.md)."""

    batch_size: int = Field(default=10, gt=0, le=50)


class BatchRecommendationResponse(BaseModel):
    """Response body for POST /recommend/batch -- up to batch_size ranked,
    guardrail-allowed ads, already sorted by relevance (highest first)."""

    user_id: str
    items: list[FeedItem]


class ImpressionRequest(BaseModel):
    """Request body for POST /events/impression -- fired client-side (via an
    Intersection Observer) when a feed item actually scrolls into view. Pure
    DB insert: no profile nudge, no budget debit -- this only exists so CTR/
    engagement-rate denominators are real counts, not proxies. user_id comes
    from the authenticated account, not a request field."""

    ad_id: str


class ReactionRequest(BaseModel):
    """Request body for POST /events/reaction. One endpoint for all three
    reactions (reaction is a body field, not the URL) -- matches how
    ModerationRequest.outcome already works elsewhere in this app. Each
    reaction logs an event and nudges the user's profile vector;
    like/interested also debit the campaign's budget (see
    feedback.record_feedback). user_id comes from the authenticated
    account, not a request field."""

    ad_id: str
    reaction: Literal["like", "dislike", "interested"]


class ReactionClearRequest(BaseModel):
    """Request body for DELETE /events/reaction -- removes the user's
    current reaction to an ad entirely, reversing its nudge/debit (see
    feedback.clear_feedback). A no-op if there was no reaction to remove.
    user_id comes from the authenticated account, not a request field."""

    ad_id: str


class ReportRequest(BaseModel):
    """Request body for POST /events/report. reason is free text, required
    only when category is 'other' -- the predefined categories are
    self-explanatory enough not to need it. user_id comes from the
    authenticated account, not a request field."""

    ad_id: str
    category: Literal["misleading", "offensive", "irrelevant", "spam", "other"]
    reason: str | None = None

    @model_validator(mode="after")
    def _reason_required_for_other(self) -> "ReportRequest":
        if self.category == "other" and not self.reason:
            raise ValueError("reason is required when category is 'other'")
        return self


class DoNotShowRequest(BaseModel):
    """Request body for POST /users/me/do-not-show -- a permanent per-user
    exclusion, not a learning signal (no profile nudge, no event log
    entry). Stored in the user's Pinecone metadata, checked during
    retrieval (see retrieval.py)."""

    ad_id: str


class ChatMessage(BaseModel):
    """One turn in the onboarding chat. Chat history is ephemeral -- the
    client owns and resends the full list each call, nothing is persisted
    server-side (see serving/onboarding_api.py). Reactions to shown
    candidates are folded in as ordinary "user" messages (e.g. "(I liked
    the Plumbing ad, wasn't interested in the Hardware store one.)") --
    they're real user-originated signal, just translated from taps to
    text, not a synthetic role. That's what feeds retrieved content back
    into the next generation and makes this RAG, not just retrieval."""

    role: Literal["user", "assistant"]
    content: str


class OnboardingChatRequest(BaseModel):
    """Request body for POST /onboarding/chat -- the streamed, user-visible
    conversational turn. No user_id needed: this call touches no DB/Pinecone
    state, it's pure conversation over whatever history the client sends.

    No candidates field: earlier this fed candidate content to the model so
    it could narrate what's being shown, but that turned out unreliable in
    real multi-turn conversations (tested live -- a topically-focused
    conversation reliably pulled the model's attention away from the actual
    ad content instead). The reply doesn't need to change based on whether
    candidates are shown -- the "this connects to what you said" signal now
    lives in a deterministic label the frontend renders above the cards,
    with no model involvement, so the chat call never needs to know.

    ready_to_finish is this turn's checkpoint result (call
    /onboarding/checkpoint first) -- tells the model to wrap up and guide
    the user to their feed instead of asking another question."""

    messages: list[ChatMessage]
    ready_to_finish: bool = False


class OnboardingCheckpointRequest(BaseModel):
    """Request body for POST /onboarding/checkpoint -- the non-streamed,
    structured-output side of a turn. Call this *before* /onboarding/chat:
    looks at the conversation so far, decides whether there's concrete
    enough signal to show candidates yet, seeds the profile the first time
    that happens, and pulls real candidates. user_id comes from the
    authenticated account, not a request field."""

    messages: list[ChatMessage]


class CheckpointJudgment(BaseModel):
    """Structured output the checkpoint LLM call returns. show_candidates is
    a separate gate from ready_to_finish -- a single vague reply shouldn't
    trigger a real checkpoint (embed + Pinecone retrieval) just because a
    turn happened; only once there's a concrete, specific interest signal.
    ready_to_finish can only be true once show_candidates has been true (in
    this turn or an earlier one) -- onboarding can't finish without ever
    having shown/tested a candidate."""

    show_candidates: bool
    ready_to_finish: bool
    interest_summary: str


class OnboardingCheckpointResponse(CheckpointJudgment):
    """Response body for POST /onboarding/checkpoint. candidates is empty
    unless show_candidates is true, and always empty when ready_to_finish is
    true (no point previewing a fresh batch right before handing the user
    off to their full feed). When shown, they're reactable cards -- the
    client logs an impression per card (same POST /events/impression the
    feed uses) and a reaction per response (POST /events/reaction), then
    folds the reactions into the next /onboarding/chat call as an ordinary
    user message (see ChatMessage)."""

    candidates: list[AdCandidate]


class PerformanceTotals(BaseModel):
    """Aggregate metrics across all events, all campaigns -- this dashboard
    is a window into the engine, not any one user's feed, so nothing here is
    scoped to a user_id."""

    impressions: int
    likes: int
    dislikes: int
    conversions: int  # "interested" reactions -- the real CTR-equivalent metric
    reports: int
    ctr: float  # conversions / impressions
    engagement_rate: float  # likes / impressions
    dislike_rate: float  # dislikes / impressions
    total_spend: float
    avg_cpa: float | None  # total_spend / conversions; None if there are no conversions yet


class PerformanceTrendPoint(BaseModel):
    """One day's impressions/conversions/CTR -- the "is it learning" trend
    line, bucketed by day since a demo doesn't have enough volume for a
    finer-grained window to be meaningful."""

    date: date
    impressions: int
    conversions: int
    ctr: float


class CampaignPerformance(BaseModel):
    """Per-campaign row for the breakdown table -- standard in real ad
    dashboards (Google Ads, Meta Ads Manager), and also how a report problem
    surfaces to whoever's running the system."""

    campaign_id: int
    headline: str
    status: str
    impressions: int
    likes: int
    dislikes: int
    conversions: int
    reports: int
    ctr: float
    spend: float


class PerformanceResponse(BaseModel):
    """Response body for GET /performance."""

    totals: PerformanceTotals
    trend: list[PerformanceTrendPoint]
    campaigns: list[CampaignPerformance]


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
    research_notes: str | None
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
