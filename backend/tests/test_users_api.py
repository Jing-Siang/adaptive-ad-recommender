from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import BlocklistEntry
from tests.conftest import auth_header

client = TestClient(app)


def test_do_not_show_adds_a_blocklist_entry(db, user, campaign):
    try:
        resp = client.post("/users/me/do-not-show", json={"ad_id": str(campaign.id)}, headers=auth_header(user))

        assert resp.status_code == 204
        entry = db.query(BlocklistEntry).filter_by(user_id=user.id, campaign_id=campaign.id).one_or_none()
        assert entry is not None
    finally:
        # The campaign fixture's own teardown deletes the row and would hit
        # a foreign-key violation if a blocklist_entries row still pointed
        # at it (no cascade configured -- an accidental cascade delete on a
        # real "someone deleted a campaign" event is worse than a
        # not-yet-common test cleanup step).
        db.query(BlocklistEntry).filter_by(user_id=user.id, campaign_id=campaign.id).delete()
        db.commit()


def test_do_not_show_is_idempotent_for_the_same_ad(db, user, campaign):
    """Blocklisting the same ad twice is a harmless no-op (ON CONFLICT DO
    NOTHING), not a unique-constraint error."""
    try:
        client.post("/users/me/do-not-show", json={"ad_id": str(campaign.id)}, headers=auth_header(user))
        resp = client.post("/users/me/do-not-show", json={"ad_id": str(campaign.id)}, headers=auth_header(user))

        assert resp.status_code == 204
        count = db.query(BlocklistEntry).filter_by(user_id=user.id, campaign_id=campaign.id).count()
        assert count == 1
    finally:
        db.query(BlocklistEntry).filter_by(user_id=user.id, campaign_id=campaign.id).delete()
        db.commit()


@patch("app.serving.users.delete_vector")
def test_reset_deletes_profile_vector_blocklist_and_reactions(mock_delete_vector, db, user, campaign):
    """"Restart onboarding": wipes the Pinecone profile vector, the
    account's blocklist_entries rows, and its Reaction rows, so a later
    reaction to an already-reacted ad is a true first reaction again (see
    docs/auth_plan.md for why leaving a stale Reaction row would silently
    apply a wrong, partial delta)."""
    from app.models import Reaction

    db.add(Reaction(user_id=user.id, campaign_id=campaign.id, reaction="like"))
    db.add(BlocklistEntry(user_id=user.id, campaign_id=campaign.id))
    user.onboarding_completed = True
    db.commit()

    resp = client.post("/users/me/reset", headers=auth_header(user))

    assert resp.status_code == 204
    mock_delete_vector.assert_called_once_with(str(user.id), namespace="users")
    assert db.query(Reaction).filter_by(user_id=user.id).count() == 0
    assert db.query(BlocklistEntry).filter_by(user_id=user.id).count() == 0
    db.refresh(user)
    assert user.onboarding_completed is False


@patch("app.serving.users.delete_vector")
def test_reset_is_a_noop_when_no_profile_exists(mock_delete_vector, user):
    """delete_vector is idempotent -- resetting an account that never had a
    profile is a harmless no-op, not an error."""
    resp = client.post("/users/me/reset", headers=auth_header(user))
    assert resp.status_code == 204
