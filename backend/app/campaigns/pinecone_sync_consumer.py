"""Long-running consumer: keeps the Pinecone `ads` namespace in sync with
the Postgres `campaigns` table via the Debezium CDC stream (see
docs/kafka_cdc_plan.md). Owns all campaign sync -- add/update/delete, not
just eligibility -- so Postgres is the only thing anyone writes to
directly for campaigns; Pinecone entirely converges from the Kafka log.
Run via `make kafka-consumer`; must be running for a campaign approval to
actually become servable (see docs/kafka_cdc_plan.md for the lag/ordering
tradeoffs)."""

import json
import time

from confluent_kafka import Consumer, Producer

from app.core.config import settings
from app.core.embeddings import embed_ads
from app.core.logging_utils import log_event
from app.core.vector_store import delete_vector, update_metadata, upsert_vector

TOPIC = "ad_recommender.public.campaigns"
DLQ_TOPIC = "ad_recommender.public.campaigns.dlq"
GROUP_ID = "pinecone-campaign-sync"
NAMESPACE = "ads"

# How often to log this consumer's own lag, whether or not messages are
# currently arriving -- poll(1.0) already loops roughly once/second when
# idle, so this doubles as a liveness heartbeat, not just a backlog signal.
_LAG_LOG_INTERVAL_SECONDS = 30

# The only fields baked into the embedded text (see campaigns/indexing.py) --
# a change to any other field never needs a fresh embedding.
_CREATIVE_FIELDS = ("headline", "description", "category")


def _embed_text(row: dict) -> str:
    return f"{row['headline']}. {row['description']}. Category: {row['category']}"


def _metadata_for(row: dict) -> dict:
    return {
        "headline": row["headline"],
        "description": row["description"],
        "category": row["category"],
        "campaign_id": row["id"],
        "status": row["status"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
    }


def _status_metadata_for(row: dict) -> dict:
    """Fields patched on every non-reembed write. Unlike headline/description/
    category, start_date/end_date never change after creation, so there's no
    staleness risk in refreshing them here too -- doing so (rather than only
    setting them in _metadata_for's full upsert) is what backfills them for
    campaigns that were already active before this metadata shape existed,
    the first time any event touches them, including a snapshot replay."""
    return {"status": row["status"], "start_date": row["start_date"], "end_date": row["end_date"]}


def _needs_reembed(before: dict | None, after: dict) -> bool:
    """An inactive campaign never has a Pinecone record (update_metadata is
    a no-op on an ID with nothing there yet), so crossing into "active" --
    whether for the first time or a re-approval -- always needs a real
    create-or-replace, not just a metadata patch, regardless of whether the
    creative happens to be unchanged."""
    if before is None or before["status"] != "active":
        return True
    return any(before[field] != after[field] for field in _CREATIVE_FIELDS)


def handle_event(payload: dict) -> str:
    """Apply one Debezium change event to Pinecone. Returns a short label
    for what it did, for logging. Pure function (no Kafka/network-polling
    concerns) so it's unit-testable without a real broker."""
    op = payload["op"]
    before = payload.get("before")
    after = payload.get("after")

    if op == "d":
        delete_vector(str(before["id"]), namespace=NAMESPACE)
        return "deleted"

    campaign_id = str(after["id"])

    if op == "r" and after["status"] == "active":
        # Initial snapshot of a row that's already active -- trust the
        # embedding itself was already correct before this consumer existed
        # (don't burn an OpenAI call re-confirming that), but still refresh
        # status/dates -- cheap, and it's what backfills start_date/end_date
        # onto campaigns indexed before those fields existed in this schema.
        update_metadata(campaign_id, _status_metadata_for(after), namespace=NAMESPACE)
        return "snapshot_metadata_refreshed"

    if after["status"] == "active" and _needs_reembed(before, after):
        vector = embed_ads([_embed_text(after)])[0]
        upsert_vector(campaign_id, vector, metadata=_metadata_for(after), namespace=NAMESPACE)
        return "reembedded"

    # Covers: became ineligible, and an active campaign with only an
    # irrelevant field changed. The vector is never deleted for eligibility
    # reasons -- only op:"d" (a true Postgres row removal) ever deletes.
    update_metadata(campaign_id, _status_metadata_for(after), namespace=NAMESPACE)
    return "metadata_updated"


def _log_lag(consumer: Consumer) -> None:
    """Self-reported lag, logged on a timer regardless of whether messages
    are currently arriving -- also serves as a liveness heartbeat during
    quiet periods, not just a backlog signal. Reads the broker's committed
    offset (consumer.committed) rather than the client's local position()
    cache -- position() was observed returning OFFSET_INVALID well after
    the consumer had genuinely caught up (not just in the first instant
    after startup, which is the only case this was written to handle),
    which made every such reading fall back to "the whole topic is
    backlog," a false alarm confirmed wrong against `kafka-consumer-
    groups.sh --describe`, the actual source of truth."""
    for tp in consumer.assignment():
        low, high = consumer.get_watermark_offsets(tp, cached=False)
        committed = consumer.committed([tp])[0].offset
        # committed is -1 (OFFSET_INVALID) only for a genuinely fresh group
        # that has never committed on this partition -- auto.offset.reset
        # is "earliest", so it'll actually start from `low`, making that
        # the correct baseline for lag in that one real case.
        lag = high - (committed if committed >= 0 else low)
        log_event("pinecone_sync_consumer_lag", partition=tp.partition, lag=lag)


def _send_to_dead_letter(producer: Producer, msg, exc: Exception) -> None:
    """Preserve a message that failed processing before committing past it
    -- flushed (blocking) so the original is never lost: if this write
    itself fails, the exception propagates, the original message is not
    committed, and it's simply retried on the next poll instead of
    vanishing with no trace anywhere. Pragmatic stopgap, not full
    SQS/RabbitMQ-grade dead-lettering (see docs/kafka_cdc_plan.md Phase 5)
    -- inspecting/replaying this topic is a manual operation, not built."""
    dlq_record = json.dumps(
        {
            "offset": msg.offset(),
            "partition": msg.partition(),
            "key": msg.key().decode() if msg.key() else None,
            "value": msg.value().decode() if msg.value() else None,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failed_at": time.time(),
        }
    ).encode()
    producer.produce(DLQ_TOPIC, key=msg.key(), value=dlq_record)
    producer.flush(timeout=10)


def run() -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    consumer.subscribe([TOPIC])
    log_event("pinecone_sync_consumer_started", topic=TOPIC, group_id=GROUP_ID)
    last_lag_log = 0.0
    try:
        while True:
            msg = consumer.poll(1.0)

            if time.time() - last_lag_log > _LAG_LOG_INTERVAL_SECONDS:
                _log_lag(consumer)
                last_lag_log = time.time()

            if msg is None:
                continue
            if msg.error():
                log_event("pinecone_sync_consumer_kafka_error", error=str(msg.error()))
                continue
            if msg.value() is None:
                # Debezium tombstone, follows every real delete for compaction -- nothing to act on.
                consumer.commit(msg)
                continue
            try:
                payload = json.loads(msg.value())["payload"]
                action = handle_event(payload)
                row = payload.get("after") or payload.get("before") or {}
                log_event("pinecone_sync_event", action=action, campaign_id=row.get("id"), op=payload["op"])
            except Exception as exc:
                _send_to_dead_letter(producer, msg, exc)
                log_event(
                    "pinecone_sync_consumer_processing_error",
                    offset=msg.offset(),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            consumer.commit(msg)
    finally:
        consumer.close()


if __name__ == "__main__":
    run()
