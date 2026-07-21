from app.core.embeddings import embed_ads
from app.core.vector_store import upsert_vector
from app.models import Campaign


def index_campaign(campaign: Campaign) -> None:
    """Embed an approved campaign's creative and upsert it into the `ads` namespace,
    making it eligible to be surfaced by retrieve_candidates."""
    text = f"{campaign.headline}. {campaign.description}. Category: {campaign.category}"
    vector = embed_ads([text])[0]
    upsert_vector(
        str(campaign.id),
        vector,
        metadata={
            "headline": campaign.headline,
            "description": campaign.description,
            "category": campaign.category,
            "campaign_id": campaign.id,
            "status": campaign.status,
        },
        namespace="ads",
    )
