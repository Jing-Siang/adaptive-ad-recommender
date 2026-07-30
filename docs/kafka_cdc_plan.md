# Kafka + Debezium CDC plan: Postgres campaign eligibility -> Pinecone

Phase 0 + Phase 1 are done and live-verified (see "Status" at the bottom).
This is the working TODO list for wiring up real CDC (Change Data Capture)
so `campaigns` table eligibility (status/budget) propagates into Pinecone
automatically instead of relying purely on `retrieval.py`'s post-filter +
oversample-and-retry.

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

- [x] Consume all `campaigns` table changes on one topic; filter in the
      consumer to the fields that actually affect eligibility (`status`,
      `budget_spent`, `budget_total`), not a topic per field.
- [x] Use `pgoutput` as the Debezium plugin (built into Postgres 10+ natively
      -- avoids installing `wal2json`/`decoderbufs` extensions).
- [x] JSON (not Avro) for the Kafka message format -- avoids standing up a
      Schema Registry at this project's scale.
- [x] Enable **log compaction** on the topic, keyed by `campaign_id` -- only
      the latest eligibility state per campaign matters, not full history.
- [ ] Decide the "ineligible" action: delete the Pinecone vector outright
      vs. set an `eligible: false` metadata flag and filter on it. Leaning
      delete -- simpler, matches "the index only ever contains truly-
      servable ads." (Still open -- this is a Phase 2 decision, not needed
      to stand up the infra itself.)

## Phase 1 -- local infra (docker-compose)

- [x] Add a Kafka broker in **KRaft mode** (no Zookeeper -- current best
      practice; Zookeeper is being phased out in Kafka 4.x). Used
      `apache/kafka:4.3.1` (verified as current stable at implementation
      time -- the plan's original `4.0.0` was just a placeholder).
- [x] Add a `debezium/connect` service (bundles Kafka Connect + the
      Postgres connector plugin already, no manual JAR install needed).
      Used `quay.io/debezium/connect:3.6` (Docker Hub's `debezium/connect`
      is stale; `quay.io` is the maintained location; `3.6` is newer than
      the plan's original `3.5` placeholder).
- [x] Reconfigure the Postgres service: `wal_level=logical`, bump
      `max_wal_senders`/`max_replication_slots`. Requires
      `docker compose up -d postgres`, **not** `restart` -- restart reuses
      the already-running container's stale `command:`, only recreating the
      container picks up a changed one. The `postgres_data` named volume is
      untouched either way, no data loss. Verified live (Compose logged
      "Recreate"/"Recreated", and `SELECT relreplident FROM pg_class WHERE
      relname='campaigns'` plus a real UPDATE confirmed it took effect).
- [x] Register the Debezium connector via a REST POST to Connect's API
      (`http://connect:8083/connectors`) -- not declared in docker-compose
      itself, it's a runtime registration step. Scripted as
      `make kafka-register-connector`, verified idempotent (`201` first
      run, `409` on re-run).

### Bugs found during live verification (not catchable by planning alone)

These only showed up once the stack was actually running -- worth recording
so a future Phase 2 implementer (or anyone recreating this stack) doesn't
hit them cold:

1. **`KAFKA_CLUSTER_ID` is silently ignored -- use unprefixed `CLUSTER_ID`.**
   The plan (and most Kafka docs/examples) suggest `KAFKA_CLUSTER_ID`, but
   the `apache/kafka` image's entrypoint only reads a small set of
   *unprefixed* vars for its own bootstrap logic -- `CLUSTER_ID` is one of
   them, separate from the `KAFKA_*`-prefixed vars it translates into
   `server.properties`. Setting `KAFKA_CLUSTER_ID` produced no error; the
   container just logged `CLUSTER_ID not set. Setting it to default value`
   and picked a random ID instead, which then mismatched the ID already
   baked into the `kafka_data` volume on the next restart (`Cluster ID does
   not match` failure). Fix: use `CLUSTER_ID` (unprefixed) in
   `docker-compose.yml`. Required wiping the `kafka_data` volume once to
   recover from the bad auto-generated ID already stored there.
2. **`localhost:9092` doesn't work from *inside* the `kafka` container --
   use `kafka:9092` even there.** `KAFKA_LISTENERS` binds the internal
   listener specifically to `PLAINTEXT://kafka:9092`, not `0.0.0.0`, so
   `kafka-topics.sh`/`kafka-cluster.sh` invocations run via
   `docker compose exec kafka ...` must also target `kafka:9092`, not
   `localhost:9092`, despite running "inside" that same container. Fixed
   in both the `Makefile`'s `kafka-register-connector` target and the
   verification commands below.

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

Phase 0 + Phase 1 done and live-verified (2026-07-30):

- `wal_level=logical` set, `REPLICA IDENTITY FULL` applied and functionally
  confirmed (an UPDATE's `payload.before.budget_spent` came through as
  `0.0`, not null).
- Kafka (KRaft, single node) and Kafka Connect running, cluster ID pinned
  correctly after fixing the `CLUSTER_ID` bug above.
- Topic `ad_recommender.public.campaigns` created with
  `cleanup.policy=compact`.
- Connector `ad-recommender-campaigns` registered and `RUNNING`; initial
  snapshot (`op:"r"`) events observed for already-seeded campaigns; a live
  `UPDATE` correctly produced an `op:"u"` event with correct before/after
  values within seconds.
- `make kafka-register-connector` confirmed idempotent (`409` on re-run).

Not yet committed to git (`docker-compose.yml`, `Makefile`,
`kafka/connectors/campaigns-connector.json`, the `REPLICA IDENTITY FULL`
migration) -- pending user go-ahead.

Next step when resumed: Phase 2, the Python consumer that actually reads
these Kafka events and updates/deletes Pinecone vectors.
