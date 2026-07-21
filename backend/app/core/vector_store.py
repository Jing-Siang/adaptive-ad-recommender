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


def fetch_vector(vector_id: str, namespace: str) -> list[float] | None:
    index = get_index()
    result = index.fetch(ids=[vector_id], namespace=namespace)
    vector = result.vectors.get(vector_id)
    return vector.values if vector else None


def upsert_vector(vector_id: str, values: list[float], metadata: dict, namespace: str) -> None:
    index = get_index()
    index.upsert(vectors=[{"id": vector_id, "values": values, "metadata": metadata}], namespace=namespace)
