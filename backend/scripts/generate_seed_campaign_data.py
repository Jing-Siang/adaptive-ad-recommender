"""One-time (or occasional) LLM generation step for the demo seed catalog --
writes data/seed_campaigns.json. Re-run to add another batch on top of
whatever's already there (the default, and the safe choice -- see --overwrite
below); the actual environment-loading step is seed_demo_campaigns.py, which
reads this file and needs no LLM call for content, only for embedding.

Splitting generation (this script) from loading (seed_demo_campaigns.py)
means re-seeding a reset dev environment is free and deterministic -- it
reads whatever's already in the JSON file instead of re-generating a
different (LLM output isn't deterministic) and non-free catalog every time.
The JSON file is checked into git as a versioned, human-reviewable fixture,
same as any other seed data.

Two-phase generation per category, not one flat prompt asking for
everything at once: first list a handful of distinct sub-niches within the
category (e.g. "adjustable dumbbell sets" / "wearable heart-rate monitors"
under "fitness", not just "fitness"), then generate a detailed batch per
sub-niche. A single huge one-shot generation tends to degrade into
repetitive, generic copy well before reaching hundreds of items; narrowing
the theme per call keeps each batch's items genuinely distinct and lets the
prompt demand concrete, specific detail (a named product, a real spec, a
price) instead of vague marketing superlatives.

Cost: at _SUBNICHES_PER_CATEGORY=8, _CAMPAIGNS_PER_SUBNICHE=21, across the
18 categories below, that's 18 sub-niche-listing calls + 144 campaign-batch
calls = 162 gpt-4o-mini calls generating ~3024 campaigns total. Well under a
dollar at published gpt-4o-mini pricing (structured-output text generation,
no images/audio/search involved) -- confirmed via OpenAI's published
pricing, not assumed. Takes real wall-clock time to run (~150+ sequential
API calls), not something to expect to finish in seconds.
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
    # Added for this batch -- genuinely distinct from the above, not just
    # synonyms (e.g. furniture vs. home_decor: functional big-ticket items
    # vs. decorative ones; baby_products vs. parenting: retail gear vs.
    # services/education).
    "baby_products",
    "gaming",
    "furniture",
    "toys",
    "health_supplements",
    "photography",
    "musical_instruments",
    "gardening",
    "jewelry_accessories",
    "wedding_services",
    "job_training_career",
    "streaming_entertainment",
    "insurance",
    "moving_relocation",
]
_SUBNICHES_PER_CATEGORY = 8
_CAMPAIGNS_PER_SUBNICHE = 12  # 8 * 12 = 96/category * 32 categories ~= 3072 total


class SubnicheList(BaseModel):
    subniches: list[str]


class SeedCampaignIdea(BaseModel):
    headline: str
    description: str
    objective: str


class SeedCampaignBatch(BaseModel):
    campaigns: list[SeedCampaignIdea]


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_SUBNICHE_PROMPT = """List {n} distinct, specific sub-niches or product/service types within the ad \
category '{category}' -- narrow enough that each one names a genuinely different kind of product or \
service, not just a different adjective on the same idea. Good sub-niches under 'fitness' would be \
things like 'adjustable dumbbell sets', 'wearable heart-rate monitors', 'online yoga subscriptions', \
'resistance band kits' -- not 'gym stuff' or 'fitness for beginners'. Return short phrases, a few \
words each, no duplicates."""

_CAMPAIGN_PROMPT = """Generate {n} distinct, realistic ad campaigns for the sub-niche '{subniche}' \
within the broader category '{category}'.

This is seed data for a demo ad-recommendation system -- specificity is the entire point, not polish. \
Every campaign must read like an actual product listing with a real spec sheet, not a marketing \
slogan. Compare these two for the same sub-niche ("adjustable dumbbell sets"):

BAD headline (never write like this): "Get Fit, Stay Lit: Join Our Night Workout Series!"
GOOD headline (write like this): "FlexCore 5-52.5lb Adjustable Dumbbell Set (Pair)"

BAD description (never write like this): "Experience the energy of night workouts and transform your \
fitness journey!"
GOOD description (write like this): "Each dumbbell adjusts from 5 to 52.5 lbs in 2.5 lb increments via \
a turn dial, replacing 15 pairs of fixed-weight dumbbells. Includes a floor tray; ships in 2 boxes, \
105 lbs total. $429."

The BAD examples above are a real, common failure mode -- vague, slogan-driven, no invented product \
name, no real numbers. Do not produce anything resembling them, even loosely.

For every one of the {n} campaigns:
- headline: names ONE specific product or service, with an invented brand/model name (e.g. "FlexCore \
5-52.5lb Adjustable Dumbbell Set") -- never a slogan, tagline, or event/series name. Under 100 \
characters.
- description (2-3 sentences): at least TWO concrete, quantifiable details (a size, weight, capacity, \
material, duration, price, or measurable spec) -- zero vague superlatives ("top-rated", "unbeatable", \
"transform your life", "unleash your inner..."). State plainly what it is and what's included, like a \
spec sheet, not an ad.
- objective: either "conversions" or "awareness".
Every campaign must be a genuinely different specific product or service within '{subniche}' -- vary \
the invented brand name, the exact numbers, and the specific angle across all {n}."""


def generate_subniches(category: str, n: int) -> list[str]:
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        input=_SUBNICHE_PROMPT.format(n=n, category=category),
        text_format=SubnicheList,
    )
    return response.output_parsed.subniches


def generate_subniche_campaigns(category: str, subniche: str, n: int) -> list[SeedCampaignIdea]:
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        input=_CAMPAIGN_PROMPT.format(n=n, subniche=subniche, category=category),
        text_format=SeedCampaignBatch,
    )
    return response.output_parsed.campaigns


def generate_all(categories: list[str] = _CATEGORIES) -> list[dict]:
    all_campaigns = []
    for category in categories:
        subniches = generate_subniches(category, _SUBNICHES_PER_CATEGORY)
        print(f"category={category}: {len(subniches)} sub-niches -- {subniches}")
        for subniche in subniches:
            ideas = generate_subniche_campaigns(category, subniche, _CAMPAIGNS_PER_SUBNICHE)
            for idea in ideas:
                all_campaigns.append(
                    {
                        "headline": idea.headline,
                        "description": idea.description,
                        "category": category,
                        "objective": idea.objective,
                    }
                )
            print(f"  {subniche}: {len(ideas)} campaigns")
    return all_campaigns


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace seed_campaigns.json entirely instead of appending to it. Destructive -- "
        "wipes whatever's already in the file. Default is to append.",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=_CATEGORIES,
        help="Only generate for these categories (space-separated) instead of all 18 -- useful for "
        "a quick sample run.",
    )
    args = parser.parse_args()

    new_campaigns = generate_all(args.categories)

    if args.overwrite or not _OUTPUT_PATH.exists():
        combined = new_campaigns
    else:
        existing = json.loads(_OUTPUT_PATH.read_text())
        combined = existing + new_campaigns

    _OUTPUT_PATH.write_text(json.dumps(combined, indent=2) + "\n")
    print(f"\nWrote {len(combined)} total campaigns ({len(new_campaigns)} new) to {_OUTPUT_PATH}")
