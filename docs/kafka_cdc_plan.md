# Kafka + Debezium CDC plan: Postgres campaign eligibility -> Pinecone

Not started yet -- this is the working TODO list for wiring up real CDC
(Change Data Capture) so `campaigns` table eligibility (status/budget)
propagates into Pinecone automatically instead of relying purely on
`retrieval.py`'s post-filter + oversample-and-retry.

**Why Kafka here and not just Redis/RQ**: campaign *review* jobs (RQ) are
independent of each other -- reviewing campaign #47 has no relationship to
reviewing campaign #48, so order doesn't matter and a per-job queue with
retry/dead-lettering is the right fit. Eligibility *sync* is different:
for a single campaign, events must be applied in the order they happened
(e.g. two budget debits crossing the exhaustion threshold -- seeing
"still active" then "now completed" in the wrong order could wrongly leave
a completed campaign servable). Kafka guarantees strict per-partition
ordering, which is exactly the property needed here (partition by
`campaign_id`) and isn't what a task queue is built around. Redis/RQ stays
as-is for campaign review -- Kafka is additive, not a Redis replacement.

**Why CDC instead of a manual dual-write**: a manual "update Pinecone right
after the Postgres commit" in application code is failure-prone -- a
crash/network blip between the two writes silently loses the sync forever,
with nothing to catch it. CDC derives the sync from Postgres's own durable
write-ahead log instead of trusting application code to remember, so a
crash mid-sync just resumes from where it left off rather than losing the
update. This does NOT replace `_eligible_campaign_ids`'s Postgres check or
the adaptive retry in `retrieve_candidates` -- those stay as a correctness
safety net for whatever small propagation lag remains.

## Phase 0 -- design decisions (lock in before building)

- [ ] Consume all `campaigns` table changes on one topic; filter in the
      consumer to the fields that actually affect eligibility (`status`,
      `budget_spent`, `budget_total`), not a topic per field.
- [ ] Use `pgoutput` as the Debezium plugin (built into Postgres 10+ natively
      -- avoids installing `wal2json`/`decoderbufs` extensions).
- [ ] JSON (not Avro) for the Kafka message format -- avoids standing up a
      Schema Registry at this project's scale.
- [ ] Enable **log compaction** on the topic, keyed by `campaign_id` -- only
      the latest eligibility state per campaign matters, not full history.
- [ ] Decide the "ineligible" action: delete the Pinecone vector outright
      vs. set an `eligible: false` metadata flag and filter on it. Leaning
      delete -- simpler, matches "the index only ever contains truly-
      servable ads."

## Phase 1 -- local infra (docker-compose)

- [ ] Add a Kafka broker in **KRaft mode** (no Zookeeper -- current best
      practice; Zookeeper is being phased out in Kafka 4.x).
- [ ] Add a `debezium/connect` service (bundles Kafka Connect + the
      Postgres connector plugin already, no manual JAR install needed).
- [ ] Reconfigure the Postgres service: `wal_level=logical`, bump
      `max_wal_senders`/`max_replication_slots`. Requires **recreating**
      the Postgres container (not just restarting) -- `wal_level` isn't
      runtime-alterable. Existing local containers from before this change
      won't have it set; note this for whoever runs `docker compose up`
      next.
- [ ] Register the Debezium connector via a REST POST to Connect's API
      (`http://connect:8083/connectors`) -- not declared in docker-compose
      itself, it's a runtime registration step. Script it as a
      `make kafka-register-connector` target so it's not a manual one-off
      every time the stack is recreated.

## Phase 2 -- consumer service

- [ ] New long-running Python process:
      `backend/app/campaigns/eligibility_sync_consumer.py`, using
      `confluent-kafka` (standard maintained Python client, add to
      `requirements.txt`).
- [ ] Add as its own docker-compose service + `make kafka-consumer` target
      -- it's a persistent streaming loop, not an RQ job, so it doesn't fit
      the existing worker pattern/`rq worker` invocation.
- [ ] Parse Debezium's envelope shape (`payload.before` / `payload.after` /
      `payload.op` -- c/u/d/r for create/update/delete/read-snapshot).
- [ ] For each event: compute eligibility from the `after` row image,
      delete/update the Pinecone vector accordingly. Must be idempotent --
      reprocessing the same event twice (restart, at-least-once delivery)
      should be harmless. Upsert/delete already are.
- [ ] Decide + implement dead-letter handling for malformed events (see
      Phase 5) so one bad event can't wedge the whole sync.

## Phase 3 -- backfill existing catalog

- [ ] Debezium's initial connector registration does a full snapshot of
      the existing table (emitted as synthetic "read" events) -- confirm
      the consumer handles these identically to live events, so it
      corrects any drift that accumulated *before* CDC existed (e.g.
      already-completed campaigns still sitting in Pinecone today).

## Phase 4 -- update retrieval.py to actually benefit from this

- [ ] Keep `_eligible_campaign_ids`'s Postgres check as a cheap final
      safety net (there's always some propagation lag, however small).
- [ ] Shrink `_INITIAL_OVERSAMPLE_FACTOR` now that Pinecone's own results
      should mostly already be eligible -- but keep the adaptive retry
      logic regardless (never fully trust a freshness guarantee blindly).

## Phase 5 -- operational basics

- [ ] Log/monitor Kafka consumer-group lag (cheap, standard "is the sync
      keeping up" signal).
- [ ] Dead-letter handling for malformed events / transient Pinecone
      failures: Kafka has no built-in "skip just this one message, keep
      the rest flowing" primitive the way SQS/RabbitMQ dead-lettering
      does -- a partition is strictly ordered, and not committing past a
      bad message blocks everything behind it in that partition too. Catch
      the error, publish the raw bad message to a separate dead-letter
      topic, then commit past it -- don't just let a bad message wedge the
      whole sync.

## Phase 6 -- testing

- [ ] Unit test the consumer's event-parsing + eligibility-decision
      function in isolation (pure function, easy with a fake Debezium
      payload).
- [ ] One integration test: run the full local stack, trigger a budget-
      exhaustion via the real API, assert the Pinecone vector for that
      campaign disappears within a few seconds.

## Status

Not started. Next step when resumed: Phase 0's design decisions, then
Phase 1's docker-compose changes.
