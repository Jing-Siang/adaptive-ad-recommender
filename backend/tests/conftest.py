import uuid
from datetime import date

import pytest

from app.core.db import SessionLocal
from app.models import Advertiser, Campaign, User


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
def user(db):
    """A real account -- reactions.user_id is a real FK now (see
    docs/auth_plan.md), so tests need an actual User row to react as,
    not a freeform string. Unique google_sub/email per call (not a fixed
    value like the advertiser fixture uses) since those columns are
    unique-constrained -- a prior test's failed teardown shouldn't be
    able to collide with this one."""
    u = User(
        google_sub=f"test-google-sub-{uuid.uuid4()}",
        email=f"pytest-{uuid.uuid4()}@example.com",
        display_name="Pytest User",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.delete(u)
    db.commit()


@pytest.fixture
def moderator_user(db):
    """Same shape as `user`, but pre-assigned the moderator role -- for
    tests exercising role-gated endpoints (see docs/auth_plan.md). Role
    assignment is manual/direct in this app, so a fixture just setting the
    column mirrors exactly how it'd really be granted."""
    u = User(
        google_sub=f"test-google-sub-{uuid.uuid4()}",
        email=f"pytest-moderator-{uuid.uuid4()}@example.com",
        display_name="Pytest Moderator",
        role="moderator",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.delete(u)
    db.commit()


@pytest.fixture
def advertiser_user(db):
    u = User(
        google_sub=f"test-google-sub-{uuid.uuid4()}",
        email=f"pytest-advertiser-{uuid.uuid4()}@example.com",
        display_name="Pytest Advertiser",
        role="advertiser",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.delete(u)
    db.commit()


def auth_header(user: User) -> dict[str, str]:
    """Shared by tests exercising role-gated endpoints -- not a fixture
    itself (needs a specific user passed in), just a small helper around
    create_access_token so tests don't each hand-build the header."""
    from app.core.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


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
