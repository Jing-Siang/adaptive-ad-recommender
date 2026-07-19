from pydantic import BaseModel, Field


class Ad(BaseModel):
    ad_id: str
    title: str
    description: str
    category: str
    price: float | None = None


class AdCandidate(Ad):
    similarity_score: float


class UserProfile(BaseModel):
    user_id: str
    interest_summary: str
    profile_vector: list[float] | None = None


class RankedAd(BaseModel):
    ad_id: str
    relevance_score: float = Field(ge=0, le=1)
    justification: str


class RankingResponse(BaseModel):
    rankings: list[RankedAd]


class GuardrailResult(BaseModel):
    ad_id: str
    allowed: bool
    reason: str | None = None


class RecommendationRequest(BaseModel):
    user_id: str
    top_k: int = 10


class RecommendationTrace(BaseModel):
    user_id: str
    candidates: list[AdCandidate]
    rankings: list[RankedAd]
    guardrail_results: list[GuardrailResult]
    served_ad_id: str | None


class FeedbackEvent(BaseModel):
    user_id: str
    ad_id: str
    outcome: str = Field(pattern="^(click|no_click|conversion)$")
