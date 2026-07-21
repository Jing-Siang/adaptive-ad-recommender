from functools import lru_cache

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.config import settings
from app.schemas import AdCandidate, RankedAd, RankingResponse


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_SYSTEM_PROMPT = """You are an ad re-ranking engine. Given a user's context and a list of \
candidate ads (already filtered by vector similarity), reason about the user's *intent* \
rather than surface-level topic similarity, and score each candidate's relevance."""


@retry(stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=20), reraise=True)
def _call_llm(user_context: str, candidates: list[AdCandidate]) -> RankingResponse:
    candidates_text = "\n".join(
        f"- id={c.ad_id} title={c.title!r} category={c.category} "
        f"similarity={c.similarity_score:.3f} description={c.description!r}"
        for c in candidates
    )
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        instructions=_SYSTEM_PROMPT,
        input=f"User context:\n{user_context}\n\nCandidates:\n{candidates_text}",
        text_format=RankingResponse,
    )
    return response.output_parsed


def rerank(user_context: str, candidates: list[AdCandidate]) -> list[RankedAd]:
    """Pass top-K candidates + user context to the LLM via OpenAI's Responses API,
    with the JSON schema enforced during generation (text_format=RankingResponse)
    rather than parsed-and-validated after the fact."""
    return _call_llm(user_context, candidates).rankings
