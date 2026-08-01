"""Generic Pinecone client + vector read/write primitives, shared by both the
serving pipeline (retrieval.py) and the campaigns pipeline (indexing.py)."""

from functools import lru_cache

from pinecone import Pinecone

from app.core.config import settings


@lru_cache
def _get_client() -> Pinecone:
    return Pinecone(api_key=settings.pinecone_api_key)


@lru_cache
def get_index():
    """Cached like _get_client() -- a fresh Index() per call was paying
    ~800ms-1s of avoidable connection-setup overhead on every Pinecone
    operation (measured: ~1.1s fresh vs ~0.3s reused)."""
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
    """Full record replace -- any metadata field not included here is wiped,
    even if it existed before (Pinecone's documented upsert behavior). Use
    for creating a record or intentionally replacing it wholesale."""
    index = get_index()
    index.upsert(vectors=[{"id": vector_id, "values": values, "metadata": metadata}], namespace=namespace)


def update_vector(vector_id: str, values: list[float], namespace: str) -> None:
    """Partial update -- only the vector values change; existing metadata
    (e.g. a user's interest_summary) is left untouched, unlike upsert_vector."""
    index = get_index()
    index.update(id=vector_id, values=values, namespace=namespace)


def update_metadata(vector_id: str, metadata: dict, namespace: str) -> None:
    """Partial update -- only the given metadata fields are set (or added);
    any other existing metadata field (or the vector's own values) is left
    untouched. Used for e.g. appending to a user's blocklist without
    disturbing interest_summary or the profile vector. No-op if the ID has
    no existing record -- it can only edit something that's already there,
    never create it (see pinecone_sync_consumer.py)."""
    index = get_index()
    index.update(id=vector_id, set_metadata=metadata, namespace=namespace)


def delete_vector(vector_id: str, namespace: str) -> None:
    """Idempotent -- deleting a nonexistent ID is a no-op in Pinecone.
    Used by pinecone_sync_consumer.py only for true row removal (op:"d")
    -- ineligible-but-still-existing campaigns are handled by
    update_metadata instead, see the consumer's decision table."""
    index = get_index()
    index.delete(ids=[vector_id], namespace=namespace)
