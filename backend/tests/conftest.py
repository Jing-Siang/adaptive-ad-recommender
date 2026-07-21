from datetime import date

import pytest

from app.core.db import SessionLocal
from app.models import Advertiser, Campaign


@pytest.fixture
def db():
    """A real session against the dev Postgres (same one docker-compose runs).
    Tests that use this create/delete their own rows -- no SAVEPOINT-based
    rollback isolation, matching how this project has been tested live
    throughout: real writes, explicit cleanup."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def advertiser(db):
    adv = Advertiser(name="Test Advertiser (pytest)")
    db.add(adv)
    db.commit()
    db.refresh(adv)
    yield adv
    db.delete(adv)
    db.commit()


@pytest.fixture
def campaign(db, advertiser):
    """An active, budgeted campaign ready to serve -- the common case most
    tests want. Tests needing a different status/budget should build their
    own Campaign instead of using this fixture."""
    c = Campaign(
        advertiser_id=advertiser.id,
        headline="Test Headline",
        description="Test description for pytest fixtures",
        category="hardware",
        objective="conversions",
        budget_total=10.0,
        budget_spent=0.0,
        start_date=date(2020, 1, 1),
        end_date=date(2099, 1, 1),
        excluded_categories=[],
        status="active",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    yield c
    db.delete(c)
    db.commit()
