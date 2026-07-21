from datetime import date
from functools import lru_cache

from pinecone import Pinecone
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.embeddings import embed_ads, embed_query
from app.models import Campaign
from app.schemas import AdCandidate

# How many extra Pinecone matches to pull beyond top_k, since some will be
# filtered out by campaign eligibility (budget/dates/status) after the fact.
_OVERSAMPLE_FACTOR = 3


@lru_cache
def _get_client() -> Pinecone:
    return Pinecone(api_key=settings.pinecone_api_key)


def get_index():
    return _get_client().Index(settings.pinecone_index_name)


def _eligible_campaign_ids(db: Session, candidate_ids: list[int]) -> set[int]:
    """Campaigns are only eligible to serve if still active, within budget, and
    within their date window -- checked against Postgres (the source of truth),
    not Pinecone metadata, which is never kept authoritative for this."""
    if not candidate_ids:
        return set()
    today = date.today()
    rows = (
        db.query(Campaign.id)
        .filter(
            Campaign.id.in_(candidate_ids),
            Campaign.status == "active",
            Campaign.budget_spent < Campaign.budget_total,
            Campaign.start_date <= today,
            Campaign.end_date >= today,
        )
        .all()
    )
    return {row.id for row in rows}


def retrieve_candidates(db: Session, user_profile_text: str, top_k: int = 10) -> list[AdCandidate]:
    """Cheap first-pass retrieval: embed the user profile, query the `ads` namespace
    for the nearest ads by cosine similarity, then keep only campaigns still
    eligible to serve (active, budgeted, in-date). No LLM call."""
    vector = embed_query([user_profile_text])[0]
    index = get_index()
    result = index.query(
        vector=vector,
        top_k=top_k * _OVERSAMPLE_FACTOR,
        namespace="ads",
        include_metadata=True,
    )

    matches = result["matches"]
    candidate_ids = [int(match["id"]) for match in matches]
    eligible_ids = _eligible_campaign_ids(db, candidate_ids)

    candidates = []
    for match in matches:
        if int(match["id"]) not in eligible_ids:
            continue
        candidates.append(
            AdCandidate(
                ad_id=match["id"],
                headline=match["metadata"]["headline"],
                description=match["metadata"]["description"],
                category=match["metadata"]["category"],
                price=match["metadata"].get("price"),
                similarity_score=match["score"],
            )
        )
        if len(candidates) >= top_k:
            break
    return candidates


def fetch_vector(vector_id: str, namespace: str) -> list[float] | None:
    index = get_index()
    result = index.fetch(ids=[vector_id], namespace=namespace)
    vector = result.vectors.get(vector_id)
    return vector.values if vector else None


def upsert_user_vector(user_id: str, vector: list[float], metadata: dict) -> None:
    index = get_index()
    index.upsert(vectors=[{"id": user_id, "values": vector, "metadata": metadata}], namespace="users")


def index_campaign(campaign: Campaign) -> None:
    """Embed an approved campaign's creative and upsert it into the `ads` namespace,
    making it eligible to be surfaced by retrieve_candidates."""
    text = f"{campaign.headline}. {campaign.description}. Category: {campaign.category}"
    vector = embed_ads([text])[0]
    index = get_index()
    index.upsert(
        vectors=[
            {
                "id": str(campaign.id),
                "values": vector,
                "metadata": {
                    "headline": campaign.headline,
                    "description": campaign.description,
                    "category": campaign.category,
                    "campaign_id": campaign.id,
                    "status": campaign.status,
                },
            }
        ],
        namespace="ads",
    )
