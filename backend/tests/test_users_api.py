from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.serving.users.upsert_vector")
@patch("app.serving.users.embed_query", return_value=[[0.1, 0.2, 0.3]])
def test_create_user_embeds_and_upserts_profile(mock_embed_query, mock_upsert_vector):
    resp = client.post("/users", json={"user_id": "pytest-user-1", "interest_summary": "loves home repair"})

    assert resp.status_code == 201
    assert resp.json() == {"user_id": "pytest-user-1", "interest_summary": "loves home repair"}
    mock_embed_query.assert_called_once_with(["loves home repair"])
    mock_upsert_vector.assert_called_once_with(
        "pytest-user-1",
        [0.1, 0.2, 0.3],
        metadata={"interest_summary": "loves home repair"},
        namespace="users",
    )


@patch("app.serving.users.fetch_metadata", return_value={"interest_summary": "loves home repair"})
def test_get_user_returns_profile(mock_fetch_metadata):
    resp = client.get("/users/pytest-user-1")

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "pytest-user-1", "interest_summary": "loves home repair"}


@patch("app.serving.users.fetch_metadata", return_value=None)
def test_get_user_not_found_returns_404(mock_fetch_metadata):
    resp = client.get("/users/no-such-user")

    assert resp.status_code == 404


@patch("app.serving.users.update_metadata")
@patch("app.serving.users.fetch_metadata", return_value={"interest_summary": "x", "blocklist": ["5"]})
def test_do_not_show_appends_to_existing_blocklist(mock_fetch_metadata, mock_update_metadata):
    resp = client.post("/users/pytest-user-1/do-not-show", json={"ad_id": "7"})

    assert resp.status_code == 204
    mock_update_metadata.assert_called_once()
    args, kwargs = mock_update_metadata.call_args
    assert args[0] == "pytest-user-1"
    assert set(kwargs["metadata"]["blocklist"]) == {"5", "7"}
    assert kwargs["namespace"] == "users"


@patch("app.serving.users.fetch_metadata", return_value=None)
def test_do_not_show_not_found_returns_404(mock_fetch_metadata):
    resp = client.post("/users/no-such-user/do-not-show", json={"ad_id": "7"})
    assert resp.status_code == 404
