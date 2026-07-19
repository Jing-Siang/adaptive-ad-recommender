from app.schemas import Ad, GuardrailResult

# Rule-based category exclusions: {ad_category: set(context_categories it must not appear next to)}
CATEGORY_EXCLUSIONS: dict[str, set[str]] = {
    "alcohol": {"sensitive", "health", "recovery"},
    "gambling": {"sensitive", "finance_distress"},
}


def check_guardrails(ad: Ad, context_categories: set[str]) -> GuardrailResult:
    """Rule-based brand-safety filter. Runs after re-ranking, before an ad is served."""
    excluded_contexts = CATEGORY_EXCLUSIONS.get(ad.category, set())
    conflict = excluded_contexts & context_categories
    if conflict:
        return GuardrailResult(
            ad_id=ad.ad_id,
            allowed=False,
            reason=f"category '{ad.category}' excluded from context(s): {sorted(conflict)}",
        )
    return GuardrailResult(ad_id=ad.ad_id, allowed=True)


def filter_ranked_ads(ads_by_id: dict[str, Ad], context_categories: set[str]) -> list[GuardrailResult]:
    return [check_guardrails(ad, context_categories) for ad in ads_by_id.values()]
