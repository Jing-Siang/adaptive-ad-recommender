from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.auth import create_access_token, create_refresh_token, get_current_user, require_role, revoke_refresh_token
from app.main import app
from app.models import User

client = TestClient(app)

_GOOGLE_CLAIMS = {
    "sub": "google-sub-123",
    "email": "new-user@example.com",
    "name": "New User",
    "picture": "https://example.com/avatar.png",
}


@patch("app.serving.auth_api.verify_google_id_token", return_value=_GOOGLE_CLAIMS)
def test_google_login_creates_new_account_as_end_user(mock_verify, db):
    """First-ever login for a Google account creates a User row defaulting
    to the least-privileged role -- advertiser/moderator are assigned
    manually, never granted by the login flow itself."""
    resp = client.post("/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "new-user@example.com"
    assert body["user"]["role"] == "end_user"
    assert body["access_token"]
    assert resp.cookies.get("refresh_token")

    revoke_refresh_token(resp.cookies.get("refresh_token"))
    db.query(User).filter_by(google_sub="google-sub-123").delete()
    db.commit()


@patch("app.serving.auth_api.verify_google_id_token", return_value=_GOOGLE_CLAIMS)
def test_google_login_finds_existing_account_by_google_sub(mock_verify, db):
    """A returning user logs into the *same* account (and keeps whatever
    role they'd been manually assigned) rather than getting a duplicate."""
    existing = User(google_sub="google-sub-123", email="new-user@example.com", display_name="New User", role="moderator")
    db.add(existing)
    db.commit()
    db.refresh(existing)

    resp = client.post("/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["id"] == existing.id
    assert body["user"]["role"] == "moderator"  # not reset to end_user

    revoke_refresh_token(resp.cookies.get("refresh_token"))
    db.delete(existing)
    db.commit()


def test_refresh_rotates_token_and_issues_new_access_token(db, user):
    old_refresh = create_refresh_token(user)

    resp = client.post("/auth/refresh", cookies={"refresh_token": old_refresh})

    assert resp.status_code == 200
    assert resp.json()["access_token"]
    new_refresh = resp.cookies.get("refresh_token")
    assert new_refresh and new_refresh != old_refresh

    # the old refresh token no longer works -- rotation revoked it
    replay = client.post("/auth/refresh", cookies={"refresh_token": old_refresh})
    assert replay.status_code == 401

    revoke_refresh_token(new_refresh)


def test_refresh_rejects_missing_or_invalid_cookie():
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401

    resp = client.post("/auth/refresh", cookies={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 401


def test_logout_revokes_the_refresh_token(user):
    refresh = create_refresh_token(user)

    resp = client.post("/auth/logout", cookies={"refresh_token": refresh})
    assert resp.status_code == 204

    replay = client.post("/auth/refresh", cookies={"refresh_token": refresh})
    assert replay.status_code == 401


def test_me_returns_the_authenticated_account(user):
    token = create_access_token(user)

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.id
    assert body["email"] == user.email


def test_me_requires_a_valid_token():
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_get_current_user_decodes_claims_without_a_db_hit(user):
    """The whole point of a stateless access token -- get_current_user
    returns a value built purely from the token's own claims."""
    token = create_access_token(user)
    credentials = type("Creds", (), {"credentials": token})()

    current = get_current_user(credentials)

    assert current.id == user.id
    assert current.role == user.role


def test_require_role_rejects_disallowed_roles(user):
    token = create_access_token(user)  # user fixture defaults to end_user
    credentials = type("Creds", (), {"credentials": token})()
    current = get_current_user(credentials)

    check = require_role("moderator")
    with pytest.raises(HTTPException) as exc_info:
        check(current)
    assert exc_info.value.status_code == 403


def test_require_role_allows_matching_role(user):
    token = create_access_token(user)
    credentials = type("Creds", (), {"credentials": token})()
    current = get_current_user(credentials)

    check = require_role("end_user", "moderator")
    assert check(current) is current
