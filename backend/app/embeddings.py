from functools import lru_cache

import voyageai
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.config import settings


@lru_cache
def _get_client() -> voyageai.Client:
    return voyageai.Client(api_key=settings.voyage_api_key)


@retry(stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=30))
def _embed(texts: list[str], model: str, input_type: str) -> list[list[float]]:
    result = _get_client().embed(texts, model=model, input_type=input_type)
    return result.embeddings


def embed_ads(texts: list[str]) -> list[list[float]]:
    """Embed ad copy for indexing. Uses the larger, higher-quality model."""
    return _embed(texts, model=settings.voyage_index_model, input_type="document")


def embed_query(texts: list[str]) -> list[list[float]]:
    """Embed a user profile / query. Uses the cheaper model; same embedding space as embed_ads."""
    return _embed(texts, model=settings.voyage_query_model, input_type="query")
