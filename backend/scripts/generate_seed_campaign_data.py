"""One-time (or occasional) LLM generation step for the demo seed catalog --
writes data/seed_campaigns.json. Re-run only if you want fresh/different seed
content; the actual environment-loading step is seed_demo_campaigns.py, which
reads this file and needs no LLM call for content, only for embedding.

Splitting generation (this script) from loading (seed_demo_campaigns.py)
means re-seeding a reset dev environment is free and deterministic -- it
reads whatever's already in the JSON file instead of re-generating a
different (LLM output isn't deterministic) and non-free catalog every time.
The JSON file is checked into git as a versioned, human-reviewable fixture,
same as any other seed data.

Cost: one structured-output call per category (same pattern as
data/generate_personas.py) generating _CAMPAIGNS_PER_CATEGORY campaigns each.
At this scale (18 categories x 16 campaigns), well under a cent -- confirmed
via OpenAI's published gpt-4o-mini pricing, not assumed.
"""

import argparse
import json
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import settings

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_campaigns.json"
_CATEGORIES = [
    "home_repair",
    "food",
    "finance",
    "electronics",
    "fitness",
    "travel",
    "fashion",
    "automotive",
    "pets",
    "beauty",
    "education",
    "real_estate",
    "parenting",
    "outdoor_recreation",
    "home_decor",
    "software_subscription",
    "alcohol",
    "gambling",
]
_CAMPAIGNS_PER_CATEGORY = 16


class SeedCampaignIdea(BaseModel):
    headline: str
    description: str
    objective: str


class SeedCampaignBatch(BaseModel):
    campaigns: list[SeedCampaignIdea]


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_PROMPT = """Generate {n} distinct, realistic ad campaign ideas for the category '{category}'. \
Each needs a punchy headline (under 100 characters), a 1-2 sentence description, and an \
objective (either "conversions" or "awareness"). Vary the tone, target audience, and specific \
offer across campaigns -- avoid near-duplicates."""


def generate_category_campaigns(category: str, n: int) -> list[SeedCampaignIdea]:
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        input=_PROMPT.format(n=n, category=category),
        text_format=SeedCampaignBatch,
    )
    return response.output_parsed.campaigns


def generate_all() -> list[dict]:
    all_campaigns = []
    for category in _CATEGORIES:
        ideas = generate_category_campaigns(category, _CAMPAIGNS_PER_CATEGORY)
        for idea in ideas:
            all_campaigns.append(
                {
                    "headline": idea.headline,
                    "description": idea.description,
                    "category": category,
                    "objective": idea.objective,
                }
            )
        print(f"Generated {len(ideas)} campaigns for category={category}")
    return all_campaigns


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    campaigns = generate_all()
    _OUTPUT_PATH.write_text(json.dumps(campaigns, indent=2) + "\n")
    print(f"\nWrote {len(campaigns)} campaigns to {_OUTPUT_PATH}")
