from unittest.mock import MagicMock, patch

from app.campaigns.pinecone_sync_consumer import _log_lag, _needs_reembed, handle_event

_MOD = "app.campaigns.pinecone_sync_consumer"


class _FakeTopicPartition:
    def __init__(self, partition: int, offset: int = 0):
        self.partition = partition
        self.offset = offset


def _mock_consumer(*, committed_offset: int, low: int, high: int, partition: int = 0) -> MagicMock:
    consumer = MagicMock()
    tp = _FakeTopicPartition(partition)
    consumer.assignment.return_value = [tp]
    consumer.get_watermark_offsets.return_value = (low, high)
    consumer.committed.return_value = [_FakeTopicPartition(partition, committed_offset)]
    return consumer


class TestLogLag:
    """_log_lag must read the broker's committed offset (consumer.committed),
    not the client's local position() cache -- position() was observed
    returning OFFSET_INVALID long after the consumer had genuinely caught
    up, not just in the first instant after startup, which made every such
    reading falsely report the entire topic as backlog."""

    @patch(f"{_MOD}.log_event")
    def test_lag_is_zero_when_caught_up(self, mock_log_event):
        consumer = _mock_consumer(committed_offset=9190, low=0, high=9190)
        _log_lag(consumer)
        mock_log_event.assert_called_once_with("pinecone_sync_consumer_lag", partition=0, lag=0)

    @patch(f"{_MOD}.log_event")
    def test_lag_reflects_real_backlog(self, mock_log_event):
        consumer = _mock_consumer(committed_offset=9000, low=0, high=9190)
        _log_lag(consumer)
        mock_log_event.assert_called_once_with("pinecone_sync_consumer_lag", partition=0, lag=190)

    @patch(f"{_MOD}.log_event")
    def test_lag_falls_back_to_low_watermark_for_a_fresh_group(self, mock_log_event):
        """OFFSET_INVALID (-1) from consumer.committed means this group has
        never committed on this partition -- a genuinely fresh group, which
        (with auto.offset.reset="earliest") will actually start from `low`,
        so that's the correct baseline here, not 0."""
        consumer = _mock_consumer(committed_offset=-1, low=100, high=9190)
        _log_lag(consumer)
        mock_log_event.assert_called_once_with("pinecone_sync_consumer_lag", partition=0, lag=9090)


def _row(**overrides) -> dict:
    """A full Debezium row image with every field handle_event touches --
    real events always carry the whole row, not a diff, see
    pinecone_sync_consumer.py's own module docstring."""
    row = {
        "id": 217,
        "headline": "Original Headline",
        "description": "Original description",
        "category": "home_repair",
        "status": "active",
        "start_date": 20000,
        "end_date": 21000,
    }
    row.update(overrides)
    return row


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_delete_op_calls_delete_vector(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """op:"d" is the only case that ever deletes, and it's the only thing
    that happens -- nothing else gets touched."""
    payload = {"op": "d", "before": _row(status="active"), "after": None}

    action = handle_event(payload)

    assert action == "deleted"
    mock_delete.assert_called_once_with("217", namespace="ads")
    mock_update_metadata.assert_not_called()
    mock_upsert.assert_not_called()
    mock_embed.assert_not_called()


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_snapshot_of_active_row_refreshes_metadata_not_reembed(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """Initial snapshot (op:"r") of an already-active campaign: trust the
    existing embedding, just refresh status/dates -- never a full re-embed,
    that would burn an OpenAI call on every fresh consumer-group replay."""
    after = _row(status="active")
    payload = {"op": "r", "before": None, "after": after}

    action = handle_event(payload)

    assert action == "snapshot_metadata_refreshed"
    mock_update_metadata.assert_called_once_with(
        "217", {"status": "active", "start_date": 20000, "end_date": 21000}, namespace="ads"
    )
    mock_upsert.assert_not_called()
    mock_embed.assert_not_called()
    mock_delete.assert_not_called()


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_snapshot_of_inactive_row_corrects_drift_via_metadata(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """Initial snapshot of a campaign that's already ineligible -- this is
    Phase 3's backfill/drift-correction case (e.g. a long-completed
    campaign whose Pinecone record predates this consumer). Metadata patch
    only, never a delete -- the vector is never removed for eligibility
    reasons, only on a true row delete."""
    after = _row(status="completed")
    payload = {"op": "r", "before": None, "after": after}

    action = handle_event(payload)

    assert action == "metadata_updated"
    mock_update_metadata.assert_called_once_with(
        "217", {"status": "completed", "start_date": 20000, "end_date": 21000}, namespace="ads"
    )
    mock_upsert.assert_not_called()
    mock_embed.assert_not_called()
    mock_delete.assert_not_called()


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_create_of_pending_review_row_is_metadata_only(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """A brand-new campaign (op:"c") is always pending_review, never active
    on creation -- no embedding happens until it's actually approved."""
    after = _row(status="pending_review")
    payload = {"op": "c", "before": None, "after": after}

    action = handle_event(payload)

    assert action == "metadata_updated"
    mock_embed.assert_not_called()
    mock_upsert.assert_not_called()
    mock_update_metadata.assert_called_once_with(
        "217", {"status": "pending_review", "start_date": 20000, "end_date": 21000}, namespace="ads"
    )


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_becoming_active_always_reembeds(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """Crossing into active -- first approval or a re-approval -- always
    re-embeds, even with unchanged creative, since an inactive campaign
    never has an existing Pinecone record for a plain metadata patch to
    edit."""
    mock_embed.return_value = [[0.1, 0.2, 0.3]]
    before = _row(status="pending_review")
    after = _row(status="active")
    payload = {"op": "u", "before": before, "after": after}

    action = handle_event(payload)

    assert action == "reembedded"
    mock_embed.assert_called_once_with(["Original Headline. Original description. Category: home_repair"])
    mock_upsert.assert_called_once_with(
        "217",
        [0.1, 0.2, 0.3],
        metadata={
            "headline": "Original Headline",
            "description": "Original description",
            "category": "home_repair",
            "campaign_id": 217,
            "status": "active",
            "start_date": 20000,
            "end_date": 21000,
        },
        namespace="ads",
    )
    mock_update_metadata.assert_not_called()


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_irrelevant_field_change_while_active_skips_reembed(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """An active campaign with only an unrelated field changed (e.g.
    reviewed_by) -- metadata patch only, no wasted OpenAI call."""
    before = _row(status="active")
    after = _row(status="active")  # identical creative fields
    payload = {"op": "u", "before": before, "after": after}

    action = handle_event(payload)

    assert action == "metadata_updated"
    mock_embed.assert_not_called()
    mock_upsert.assert_not_called()
    mock_update_metadata.assert_called_once_with(
        "217", {"status": "active", "start_date": 20000, "end_date": 21000}, namespace="ads"
    )


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_creative_change_while_active_reembeds(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """An active campaign with its headline edited -- re-embeds with the
    new content."""
    mock_embed.return_value = [[0.9, 0.9, 0.9]]
    before = _row(status="active", headline="Old Headline")
    after = _row(status="active", headline="New Headline")
    payload = {"op": "u", "before": before, "after": after}

    action = handle_event(payload)

    assert action == "reembedded"
    mock_embed.assert_called_once_with(["New Headline. Original description. Category: home_repair"])
    mock_update_metadata.assert_not_called()


@patch(f"{_MOD}.embed_ads")
@patch(f"{_MOD}.upsert_vector")
@patch(f"{_MOD}.update_metadata")
@patch(f"{_MOD}.delete_vector")
def test_becoming_ineligible_never_deletes(mock_delete, mock_update_metadata, mock_upsert, mock_embed):
    """A campaign leaving active (e.g. budget exhausted -> completed) gets
    a metadata patch, never a delete -- the vector is only ever removed
    for a true Postgres row deletion (op:"d"), not for eligibility."""
    before = _row(status="active")
    after = _row(status="completed")
    payload = {"op": "u", "before": before, "after": after}

    action = handle_event(payload)

    assert action == "metadata_updated"
    mock_delete.assert_not_called()
    mock_embed.assert_not_called()
    mock_update_metadata.assert_called_once_with(
        "217", {"status": "completed", "start_date": 20000, "end_date": 21000}, namespace="ads"
    )


class TestNeedsReembed:
    """Direct tests of the "eligibility-decision" function itself, on top
    of the handle_event-level tests above."""

    def test_true_when_before_is_none(self):
        assert _needs_reembed(None, _row(status="active")) is True

    def test_true_when_becoming_active_from_pending_review(self):
        before = _row(status="pending_review")
        after = _row(status="active")
        assert _needs_reembed(before, after) is True

    def test_false_when_active_and_creative_unchanged(self):
        before = _row(status="active")
        after = _row(status="active")
        assert _needs_reembed(before, after) is False

    def test_true_when_headline_changed(self):
        before = _row(status="active", headline="Old")
        after = _row(status="active", headline="New")
        assert _needs_reembed(before, after) is True

    def test_true_when_description_changed(self):
        before = _row(status="active", description="Old")
        after = _row(status="active", description="New")
        assert _needs_reembed(before, after) is True

    def test_true_when_category_changed(self):
        before = _row(status="active", category="old_category")
        after = _row(status="active", category="new_category")
        assert _needs_reembed(before, after) is True

    def test_false_when_leaving_active_with_unchanged_creative(self):
        """_needs_reembed itself doesn't gate on after.status -- handle_event
        does that separately -- but confirming its own behavior here isn't
        misleading: it only ever answers "would re-embedding be warranted
        by these before/after values," not "should we act at all.\""""
        before = _row(status="active")
        after = _row(status="completed")
        assert _needs_reembed(before, after) is False
