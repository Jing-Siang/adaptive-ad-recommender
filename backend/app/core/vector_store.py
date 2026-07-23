"""Generic Pinecone client + vector read/write primitives, shared by both the
serving pipeline (retrieval.py) and the campaigns pipeline (indexing.py)."""

from functools import lru_cache

from pinecone import Pinecone

from app.core.config import settings


@lru_cache
def _get_client() -> Pinecone:
    return Pinecone(api_key=settings.pinecone_api_key)


def get_index():
    return _get_client().Index(settings.pinecone_index_name)


def _fetch(vector_id: str, namespace: str):
    index = get_index()
    result = index.fetch(ids=[vector_id], namespace=namespace)
    return result.vectors.get(vector_id)


def fetch_vector(vector_id: str, namespace: str) -> list[float] | None:
    record = _fetch(vector_id, namespace)
    return record.values if record else None


def fetch_metadata(vector_id: str, namespace: str) -> dict | None:
    record = _fetch(vector_id, namespace)
    return record.metadata if record else None


def upsert_vector(vector_id: str, values: list[float], metadata: dict, namespace: str) -> None:
    index = get_index()
    index.upsert(vectors=[{"id": vector_id, "values": values, "metadata": metadata}], namespace=namespace)
