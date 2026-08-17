from datetime import date

from app.models import Campaign


def test_campaign_defaults(db, advertiser_user):
    """A Campaign built without specifying status/budget_spent/excluded_categories
    should get sensible Python-side defaults, not None/errors."""
    c = Campaign(
        user_id=advertiser_user.id,
        headline="Defaults Test",
        description="checking Python-side column defaults",
        category="hardware",
        objective="conversions",
        budget_total=100.0,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    try:
        assert c.status == "pending_review"
        assert c.budget_spent == 0.0
        assert c.excluded_categories == []
        assert c.review_reason is None
        assert c.reviewed_by is None
        assert c.reviewed_at is None
        assert c.created_at is not None
    finally:
        db.delete(c)
        db.commit()


def test_campaign_excluded_categories_round_trips_as_list(db, advertiser_user):
    """excluded_categories uses Postgres ARRAY(String) -- confirm it actually
    round-trips as a real Python list, not a string or something SQLite-shaped
    (this project deliberately doesn't support SQLite, see conversation notes)."""
    c = Campaign(
        user_id=advertiser_user.id,
        headline="Array Test",
        description="checking ARRAY(String) round-trip",
        category="alcohol",
        objective="awareness",
        budget_total=50.0,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        excluded_categories=["sensitive", "health", "recovery"],
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    try:
        assert c.excluded_categories == ["sensitive", "health", "recovery"]
        assert isinstance(c.excluded_categories, list)
    finally:
        db.delete(c)
        db.commit()
