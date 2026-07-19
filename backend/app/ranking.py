import json
from functools import lru_cache

import anthropic
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.config import settings
from app.schemas import AdCandidate, RankedAd, RankingResponse


@lru_cache
def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


_SYSTEM_PROMPT = """You are an ad re-ranking engine. Given a user's context and a list of \
candidate ads (already filtered by vector similarity), reason about the user's *intent* \
rather than surface-level topic similarity, and score each candidate's relevance.

Respond with ONLY a JSON object matching this schema, no prose:
{"rankings": [{"ad_id": str, "relevance_score": float (0-1), "justification": str}]}
"""


@retry(stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=20))
def _call_claude(user_context: str, candidates: list[AdCandidate]) -> str:
    candidates_text = "\n".join(
        f"- id={c.ad_id} title={c.title!r} category={c.category} "
        f"similarity={c.similarity_score:.3f} description={c.description!r}"
        for c in candidates
    )
    message = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"User context:\n{user_context}\n\nCandidates:\n{candidates_text}",
            }
        ],
    )
    return message.content[0].text


def rerank(user_context: str, candidates: list[AdCandidate]) -> list[RankedAd]:
    """Pass top-K candidates + user context to Claude, require structured output,
    validate against the Pydantic schema before use. Retries on validation failure."""
    last_error: Exception | None = None
    for _ in range(3):
        raw = _call_claude(user_context, candidates)
        try:
            parsed = json.loads(raw)
            return RankingResponse.model_validate(parsed).rankings
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            continue
    raise ValueError(f"Claude re-ranking failed structured-output validation: {last_error}")
