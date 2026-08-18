"""Demo artifact: loads the pre-generated campaign catalog (data/seed_campaigns.json,
produced by generate_seed_campaign_data.py) into Postgres + Pinecone -- creates
real Campaign rows owned by a synthetic seed User account (proper ownership,
budget, eligibility dates -- the full data model), but sets status="active" directly
and calls index_campaign
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
from app.models import Campaign, User
from app.serving.guardrails import CATEGORY_EXCLUSIONS

# A synthetic seed account, not a real Google login -- fixed google_sub so
# re-running this script reuses the same account instead of creating a new
# one each time (matches the old Advertiser-by-fixed-name idempotency).
_SEED_GOOGLE_SUB = "seed-script"
_SEED_DISPLAY_NAME = "Demo Seed Advertiser"
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_campaigns.json"


def seed_campaigns() -> list[Campaign]:
    """Create the seed account (if needed), then create + index a Campaign
    row for every entry in data/seed_campaigns.json that isn't already in
    Postgres -- skipping the async review job entirely, same end state as a
    real approved campaign (status=active, embedded, indexed).

    "Already in Postgres" is checked by (headline, category) existing
    anywhere in the campaigns table, not by counting rows owned by the seed
    account -- the original catalog's rows ended up owned by a real user
    account instead (see docs/auth_plan.md Phase 5: the Advertiser->User
    migration backfilled every pre-existing campaign, including these, to
    whichever user existed at the time, since there was no seed account yet
    to backfill to). Counting the seed account's own rows would see 0 and
    re-create the whole original catalog as duplicates the moment this
    script runs after that migration. Content-based dedup is correct
    regardless of which account historically owns what, and makes growing
    data/seed_campaigns.json with a fresh generation batch (see
    generate_seed_campaign_data.py) and re-running this safe by default --
    no manual bookkeeping of how many rows were "already loaded"."""
    seed_data = json.loads(_DATA_PATH.read_text())

    db = SessionLocal()
    created = []
    try:
        seed_user = db.query(User).filter_by(google_sub=_SEED_GOOGLE_SUB).first()
        if seed_user is None:
            seed_user = User(
                google_sub=_SEED_GOOGLE_SUB,
                email="seed-script@example.com",
                display_name=_SEED_DISPLAY_NAME,
                role="advertiser",
            )
            db.add(seed_user)
            db.flush()

        existing_pairs = {(h, c) for h, c in db.query(Campaign.headline, Campaign.category).all()}

        today = date.today()
        for entry in seed_data:
            key = (entry["headline"], entry["category"])
            if key in existing_pairs:
                continue
            excluded_categories = sorted(CATEGORY_EXCLUSIONS.get(entry["category"], set()))
            campaign = Campaign(
                user_id=seed_user.id,
                headline=entry["headline"],
                description=entry["description"],
                category=entry["category"],
                objective=entry["objective"],
                budget_total=100.0,
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
            existing_pairs.add(key)
        db.commit()
        if not created:
            print(f"All {len(seed_data)} entries in the seed file are already loaded -- nothing to do.")
    finally:
        db.close()
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    campaigns = seed_campaigns()
    print(f"Seeded {len(campaigns)} new campaign(s) total.")
