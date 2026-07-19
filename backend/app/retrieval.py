from functools import lru_cache

from pinecone import Pinecone

from app.config import settings
from app.embeddings import embed_query
from app.schemas import AdCandidate


@lru_cache
def _get_client() -> Pinecone:
    return Pinecone(api_key=settings.pinecone_api_key)


def get_index():
    return _get_client().Index(settings.pinecone_index_name)


def retrieve_candidates(user_profile_text: str, top_k: int = 10) -> list[AdCandidate]:
    """Cheap first-pass retrieval: embed the user profile, query the `ads` namespace
    for the top-K nearest ads by cosine similarity. No LLM call."""
    vector = embed_query([user_profile_text])[0]
    index = get_index()
    result = index.query(
        vector=vector,
        top_k=top_k,
        namespace="ads",
        include_metadata=True,
    )
    return [
        AdCandidate(
            ad_id=match["id"],
            title=match["metadata"]["title"],
            description=match["metadata"]["description"],
            category=match["metadata"]["category"],
            price=match["metadata"].get("price"),
            similarity_score=match["score"],
        )
        for match in result["matches"]
    ]


def upsert_user_vector(user_id: str, vector: list[float], metadata: dict) -> None:
    index = get_index()
    index.upsert(vectors=[{"id": user_id, "values": vector, "metadata": metadata}], namespace="users")
