"""One real, unmocked, end-to-end test of the Kafka CDC pipeline -- not
part of the default `make test` run (see the `integration` marker in
pyproject.toml). Preconditions this test assumes and does NOT provision
itself: `make infra`, `make kafka`, and `make kafka-register-connector`
already run, same as the rest of the suite already assumes Postgres/Redis
are up. Run explicitly via `make test-integration`.

Costs real (tiny) money: one real OpenAI embedding call via
index_campaign(), and the consumer's own re-embed/metadata-patch calls."""

import subprocess
import sys
import time
import uuid
from datetime import date

import pytest
from confluent_kafka import Consumer, TopicPartition
from fastapi.testclient import TestClient

from app.campaigns.indexing import index_campaign
from app.campaigns.pinecone_sync_consumer import GROUP_ID, TOPIC
from app.core.config import settings
from app.core.vector_store import delete_vector, fetch_metadata, fetch_vector
from app.main import app
from app.models import Campaign, Event, User

client = TestClient(app)


def _seek_consumer_group_to_latest() -> None:
    """pinecone-campaign-sync is a persistent group -- by the time this
    test runs it may have real unrelated backlog sitting in front of it
    from other testing, which would make this test's runtime depend on
    however much history happens to exist rather than on the thing it's
    actually testing. Force the group's committed offset to the topic's
    current end first, so the consumer subprocess started below only ever
    sees this test's own event."""
    consumer = Consumer(
        {"bootstrap.servers": settings.kafka_bootstrap_servers, "group.id": GROUP_ID, "enable.auto.commit": False}
    )
    try:
        tp = TopicPartition(TOPIC, 0)
        _, high = consumer.get_watermark_offsets(tp, cached=False)
        consumer.commit(offsets=[TopicPartition(TOPIC, 0, high)])
    finally:
        consumer.close()

# A real, already-onboarded user profile already sitting in Pinecone's
# `users` namespace -- reused rather than provisioned fresh here, since
# real onboarding needs its own LLM persona-generation call, out of scope
# for a test focused on the CDC consumer's reaction to a budget event.
_REAL_USER_ID = "ac712b5f-9aaf-4ff6-8b82-5216cea4b4bc"


@pytest.mark.integration
def test_budget_exhaustion_updates_pinecone_via_consumer(db):
    """Submit->approve is skipped -- this test goes straight to an active,
    near-exhausted campaign, since the thing under test is specifically
    the consumer's reaction to a budget-exhaustion event, not the review
    flow (already covered elsewhere)."""
    submitter = User(
        google_sub=f"integration-test-{uuid.uuid4()}",
        email=f"integration-test-{uuid.uuid4()}@example.com",
        display_name="Integration Test Advertiser",
        role="advertiser",
    )
    db.add(submitter)
    db.commit()
    db.refresh(submitter)

    campaign = Campaign(
        user_id=submitter.id,
        headline="Integration Test Campaign",
        description="Exists only to prove the consumer reacts to a real budget-exhaustion event.",
        category="software",
        objective="awareness",
        budget_total=1.0,  # one "interested" reaction ($2.00) exhausts this
        budget_spent=0.0,
        start_date=date(2020, 1, 1),
        end_date=date(2099, 1, 1),
        excluded_categories=[],
        status="active",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    campaign_id = campaign.id

    index_campaign(campaign)  # seeds a real Pinecone vector, no Kafka involved

    # Pinecone upserts aren't always immediately readable (documented
    # elsewhere in this project) -- the /events/reaction call below does
    # its own fetch_vector(ad_id, ...) internally, so wait until that
    # would actually succeed rather than racing it.
    deadline = time.time() + 10
    while time.time() < deadline and fetch_vector(str(campaign_id), namespace="ads") is None:
        time.sleep(0.3)

    _seek_consumer_group_to_latest()

    consumer_proc = subprocess.Popen(
        [sys.executable, "-m", "app.campaigns.pinecone_sync_consumer"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        resp = client.post(
            "/events/reaction",
            json={"user_id": _REAL_USER_ID, "ad_id": str(campaign_id), "reaction": "interested"},
        )
        assert resp.status_code == 201

        deadline = time.time() + 15
        metadata = None
        while time.time() < deadline:
            metadata = fetch_metadata(str(campaign_id), namespace="ads")
            if metadata is not None and metadata.get("status") == "completed":
                break
            time.sleep(0.5)

        assert metadata is not None, "campaign's Pinecone vector disappeared -- should only ever be metadata-patched"
        assert metadata["status"] == "completed", (
            f"consumer did not update Pinecone's status within 15s, last seen: {metadata}"
        )
    finally:
        consumer_proc.terminate()
        consumer_proc.wait(timeout=10)
        delete_vector(str(campaign_id), namespace="ads")
        db.query(Event).filter_by(campaign_id=campaign_id).delete()
        db.delete(campaign)
        db.delete(submitter)
        db.commit()
