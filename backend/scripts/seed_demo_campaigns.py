"""Demo artifact: loads the pre-generated campaign catalog (data/seed_campaigns.json,
produced by generate_seed_campaign_data.py) into Postgres + Pinecone -- creates
real Advertiser/Campaign rows (proper ownership, budget, eligibility dates -- the
full data model), but sets status="active" directly and calls index_campaign
(embed + Pinecone upsert) directly, skipping the async policy-review job
entirely. Category exclusions (alcohol/gambling, matching serving/guardrails.py's
CATEGORY_EXCLUSIONS) are applied directly from each entry's category, not
re-derived by an LLM.

No LLM call happens in this script -- content generation is a separate,
occasional step (generate_seed_campaign_data.py); this one only loads whatever's
already in the JSON file, so it's safe and cheap to re-run anytime. The only
real cost here is embedding (text-embedding-3-small, $0.02/1M input tokens --
negligible even at this catalog's size).

Each campaign still has exactly one category, not several -- real ad platforms
often support multi-category targeting, but retrieval already works on
full-text embeddings of headline+description+category, so semantic relevance
isn't limited to an exact category match; category's only real job here is
guardrail exclusions and dashboard grouping, neither of which needs
multi-category. Revisit only if that stops being true.
"""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.campaigns.indexing import index_campaign
from app.core.db import SessionLocal
from app.core.logging_utils import log_event
from app.models import Advertiser, Campaign
from app.serving.guardrails import CATEGORY_EXCLUSIONS

_ADVERTISER_NAME = "Demo Seed Advertiser"
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_campaigns.json"


def seed_campaigns() -> list[Campaign]:
    """Create the seed advertiser (if needed), then create + index a Campaign
    row for every entry in data/seed_campaigns.json -- skipping the async
    review job entirely, same end state as a real approved campaign
    (status=active, embedded, indexed). Skips re-seeding entirely if this
    advertiser already has a full catalog, so re-running the script doesn't
    pile up duplicate batches."""
    seed_data = json.loads(_DATA_PATH.read_text())

    db = SessionLocal()
    created = []
    try:
        advertiser = db.query(Advertiser).filter_by(name=_ADVERTISER_NAME).first()
        if advertiser is None:
            advertiser = Advertiser(name=_ADVERTISER_NAME)
            db.add(advertiser)
            db.flush()
        else:
            existing_count = db.query(Campaign).filter_by(advertiser_id=advertiser.id).count()
            if existing_count >= len(seed_data):
                print(
                    f"Advertiser already has {existing_count} campaigns "
                    f"(>= {len(seed_data)} in seed file) -- skipping."
                )
                return []

        today = date.today()
        for entry in seed_data:
            excluded_categories = sorted(CATEGORY_EXCLUSIONS.get(entry["category"], set()))
            campaign = Campaign(
                advertiser_id=advertiser.id,
                headline=entry["headline"],
                description=entry["description"],
                category=entry["category"],
                objective=entry["objective"],
                budget_total=500.0,
                budget_spent=0.0,
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=365),
                excluded_categories=excluded_categories,
                status="active",
                review_reason="seed data -- live policy review skipped, category exclusions applied directly",
                reviewed_by="seed_script",
                reviewed_at=datetime.now(UTC),
            )
            db.add(campaign)
            db.flush()
            index_campaign(campaign)
            log_event("campaign_seeded", campaign_id=campaign.id, category=entry["category"])
            created.append(campaign)
        db.commit()
    finally:
        db.close()
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    campaigns = seed_campaigns()
    print(f"Seeded {len(campaigns)} new campaign(s) total.")
