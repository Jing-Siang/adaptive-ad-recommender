from datetime import UTC, date, datetime, timedelta

import pytest
from unittest.mock import MagicMock, patch

from app.models import Event
from app.serving.retrieval import _recently_shown_campaign_ids, retrieve_candidates

_TODAY_EPOCH_DAYS = (date.today() - date(1970, 1, 1)).days
_ELIGIBILITY_FILTER = {
    "status": {"$eq": "active"},
    "start_date": {"$lte": _TODAY_EPOCH_DAYS},
    "end_date": {"$gte": _TODAY_EPOCH_DAYS},
}


def test_recently_shown_campaign_ids_respects_time_window(db, campaign):
    """An impression inside the suppression window counts as recently shown;
    one from before the window (e.g. yesterday) doesn't."""
    recent = Event(
        user_id="pytest-user",
        campaign_id=campaign.id,
        event_type="impression",
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    stale = Event(
        user_id="pytest-user",
        campaign_id=campaign.id,
        event_type="impression",
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(recent)
    db.commit()

    try:
        assert _recently_shown_campaign_ids(db, "pytest-user") == {campaign.id}
    finally:
        db.delete(recent)
        db.commit()

    db.add(stale)
    db.commit()
    try:
        assert _recently_shown_campaign_ids(db, "pytest-user") == set()
    finally:
        db.delete(stale)
        db.commit()


@patch("app.serving.retrieval._recently_shown_campaign_ids", return_value=set())
@patch("app.serving.retrieval._eligible_campaign_ids", return_value={1})
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_metadata", return_value={})
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_maps_matches_to_ad_candidates(
    mock_fetch_vector, mock_fetch_metadata, mock_get_index, mock_eligible, mock_recent
):
    """A single eligible Pinecone match maps to an AdCandidate with the right
    fields; the Pinecone query asks for exactly top_k, no oversampling."""
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [
            {
                "id": "1",
                "score": 0.92,
                "metadata": {
                    "headline": "Leaky faucet repair kit",
                    "description": "Fix drips fast",
                    "category": "home_repair",
                    "price": 19.99,
                },
            }
        ]
    }
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert len(candidates) == 1
    assert candidates[0].ad_id == "1"
    assert candidates[0].similarity_score == 0.92
    mock_index.query.assert_called_once()
    _, kwargs = mock_index.query.call_args
    assert kwargs["namespace"] == "ads"
    assert kwargs["top_k"] == 5
    # status/date-window filter is unconditional now; campaign_id $nin is
    # still only added when there's something to exclude.
    assert kwargs["filter"] == _ELIGIBILITY_FILTER


@patch("app.serving.retrieval._recently_shown_campaign_ids", return_value=set())
@patch("app.serving.retrieval._eligible_campaign_ids", return_value=set())
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_metadata", return_value={})
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_empty_result(
    mock_fetch_vector, mock_fetch_metadata, mock_get_index, mock_eligible, mock_recent
):
    """No Pinecone matches at all -> an empty candidate list, not an error."""
    mock_index = MagicMock()
    mock_index.query.return_value = {"matches": []}
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert candidates == []


@patch("app.serving.retrieval._recently_shown_campaign_ids", return_value=set())
@patch("app.serving.retrieval._eligible_campaign_ids", return_value={2})
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_metadata", return_value={})
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_filters_out_ineligible_campaigns(
    mock_fetch_vector, mock_fetch_metadata, mock_get_index, mock_eligible, mock_recent
):
    """Campaign 1 is a closer vector match, but only campaign 2 is eligible
    (e.g. campaign 1's budget is exhausted or it's expired) -- it should be
    excluded even though Pinecone returned it. Eligibility stays a Python
    post-filter (can't move into Pinecone -- see _eligible_campaign_ids),
    unlike blocklist/recently-shown below."""
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [
            {"id": "1", "score": 0.95, "metadata": {"headline": "A", "description": "A", "category": "x"}},
            {"id": "2", "score": 0.80, "metadata": {"headline": "B", "description": "B", "category": "x"}},
        ]
    }
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    assert len(candidates) == 1
    assert candidates[0].ad_id == "2"


@patch("app.serving.retrieval._recently_shown_campaign_ids", return_value=set())
@patch("app.serving.retrieval._eligible_campaign_ids", return_value={2})
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_metadata", return_value={"blocklist": ["1"]})
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_passes_blocklist_as_pinecone_filter(
    mock_fetch_vector, mock_fetch_metadata, mock_get_index, mock_eligible, mock_recent
):
    """The blocklist is passed to Pinecone as a query-time $nin filter on
    campaign_id, not applied as a Python post-filter -- Pinecone's
    single-stage filtering searches past excluded IDs for real matches
    instead of risking a fixed-size batch coming up short. We can't fake
    that server-side behavior in a mock, so this test verifies the filter
    is built correctly rather than the exclusion itself."""
    mock_index = MagicMock()
    mock_index.query.return_value = {
        "matches": [{"id": "2", "score": 0.80, "metadata": {"headline": "B", "description": "B", "category": "x"}}]
    }
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    candidates = retrieve_candidates(mock_db, "user-123", top_k=5)

    _, kwargs = mock_index.query.call_args
    assert kwargs["filter"] == {**_ELIGIBILITY_FILTER, "campaign_id": {"$nin": [1]}}
    assert len(candidates) == 1
    assert candidates[0].ad_id == "2"


@patch("app.serving.retrieval._recently_shown_campaign_ids", return_value={3, 4})
@patch("app.serving.retrieval._eligible_campaign_ids", return_value=set())
@patch("app.serving.retrieval.get_index")
@patch("app.serving.retrieval.fetch_metadata", return_value={"blocklist": ["1"]})
@patch("app.serving.retrieval.fetch_vector", return_value=[0.1, 0.2, 0.3])
def test_retrieve_candidates_combines_blocklist_and_recently_shown_in_filter(
    mock_fetch_vector, mock_fetch_metadata, mock_get_index, mock_eligible, mock_recent
):
    """Blocklisted and recently-shown campaign IDs are merged into one
    $nin filter passed to Pinecone."""
    mock_index = MagicMock()
    mock_index.query.return_value = {"matches": []}
    mock_get_index.return_value = mock_index
    mock_db = MagicMock()

    retrieve_candidates(mock_db, "user-123", top_k=5)

    _, kwargs = mock_index.query.call_args
    assert kwargs["filter"] == {**_ELIGIBILITY_FILTER, "campaign_id": {"$nin": [1, 3, 4]}}


@patch("app.serving.retrieval.fetch_vector", return_value=None)
def test_retrieve_candidates_raises_when_no_profile_exists(mock_fetch_vector):
    """No fallback to embedding raw text -- a missing profile is a bug
    (onboarding should have created one already), so this raises rather
    than silently degrading to a cold-start path."""
    mock_db = MagicMock()

    with pytest.raises(ValueError, match="no profile found"):
        retrieve_candidates(mock_db, "user-with-no-profile", top_k=5)
