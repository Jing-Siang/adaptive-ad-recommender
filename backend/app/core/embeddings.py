from functools import lru_cache

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core.config import settings


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=30), reraise=True)
def _embed(texts: list[str]) -> list[list[float]]:
    result = _get_client().embeddings.create(input=texts, model=settings.openai_embedding_model)
    return [item.embedding for item in result.data]


def embed_ads(texts: list[str]) -> list[list[float]]:
    """Embed ad copy for indexing."""
    return _embed(texts)


def embed_query(texts: list[str]) -> list[list[float]]:
    """Embed a user profile / query. Unlike Voyage's index/query model split, OpenAI
    uses the same model for both — same embedding space, just named separately here
    for call-site clarity."""
    return _embed(texts)
