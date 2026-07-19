import pytest

from app.guardrails import check_guardrails
from app.schemas import Ad

ALCOHOL_AD = Ad(ad_id="ad-1", title="Craft beer club", description="Monthly beer subscription", category="alcohol")
HARDWARE_AD = Ad(ad_id="ad-2", title="Drill set", description="Cordless drill kit", category="hardware")


@pytest.mark.parametrize(
    "ad,context_categories,expected_allowed",
    [
        (ALCOHOL_AD, {"sensitive"}, False),
        (ALCOHOL_AD, {"recovery"}, False),
        (ALCOHOL_AD, {"sports"}, True),
        (HARDWARE_AD, {"sensitive"}, True),
        (HARDWARE_AD, set(), True),
    ],
)
def test_check_guardrails(ad, context_categories, expected_allowed):
    result = check_guardrails(ad, context_categories)
    assert result.allowed is expected_allowed
    assert result.ad_id == ad.ad_id
    if not expected_allowed:
        assert result.reason is not None
