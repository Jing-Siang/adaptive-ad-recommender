from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import auth_header

client = TestClient(app)


@patch("app.serving.users.update_metadata")
@patch("app.serving.users.fetch_metadata", return_value={"interest_summary": "x", "blocklist": ["5"]})
def test_do_not_show_appends_to_existing_blocklist(mock_fetch_metadata, mock_update_metadata, user):
    """New ad_id is added to the existing blocklist (not replacing it), via
    a partial metadata update that leaves interest_summary untouched."""
    resp = client.post("/users/me/do-not-show", json={"ad_id": "7"}, headers=auth_header(user))

    assert resp.status_code == 204
    mock_update_metadata.assert_called_once()
    args, kwargs = mock_update_metadata.call_args
    assert args[0] == str(user.id)
    assert set(kwargs["metadata"]["blocklist"]) == {"5", "7"}
    assert kwargs["namespace"] == "users"


@patch("app.serving.users.fetch_metadata", return_value=None)
def test_do_not_show_not_found_returns_404(mock_fetch_metadata, user):
    """Can't blocklist an ad for an account that has no profile yet."""
    resp = client.post("/users/me/do-not-show", json={"ad_id": "7"}, headers=auth_header(user))
    assert resp.status_code == 404


@patch("app.serving.users.delete_vector")
def test_reset_deletes_profile_vector_and_reactions(mock_delete_vector, db, user, campaign):
    """"Restart onboarding": wipes the Pinecone profile vector and the
    account's Reaction rows, so a later reaction to an already-reacted ad
    is a true first reaction again (see docs/auth_plan.md for why leaving
    a stale Reaction row would silently apply a wrong, partial delta)."""
    from app.models import Reaction

    db.add(Reaction(user_id=user.id, campaign_id=campaign.id, reaction="like"))
    user.onboarding_completed = True
    db.commit()

    resp = client.post("/users/me/reset", headers=auth_header(user))

    assert resp.status_code == 204
    mock_delete_vector.assert_called_once_with(str(user.id), namespace="users")
    assert db.query(Reaction).filter_by(user_id=user.id).count() == 0
    db.refresh(user)
    assert user.onboarding_completed is False


@patch("app.serving.users.delete_vector")
def test_reset_is_a_noop_when_no_profile_exists(mock_delete_vector, user):
    """delete_vector is idempotent -- resetting an account that never had a
    profile is a harmless no-op, not an error."""
    resp = client.post("/users/me/reset", headers=auth_header(user))
    assert resp.status_code == 204
