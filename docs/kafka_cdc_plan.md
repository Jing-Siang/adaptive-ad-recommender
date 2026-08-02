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
- [x] Decide the "ineligible" action: **never delete for eligibility
      reasons** -- keep the vector, patch its `status` metadata field
      instead. (Reversed from this doc's original "leaning delete" during
      Phase 2 design: deleting would strand a vector every time a later
      edit landed on an already-ineligible campaign, since Pinecone's
      partial-update call is a no-op on a missing record -- it can only
      edit something that already exists, never recreate it. Mirroring
      the row instead sidesteps that entirely; `retrieval.py`'s new
      `status` query filter is what actually keeps ineligible ads out of
      results, not the vector's existence.) The vector is only ever
      deleted for a genuine Postgres row removal (`op:"d"`).

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

- [x] New long-running Python process:
      `backend/app/campaigns/pinecone_sync_consumer.py` (renamed from the
      original `eligibility_sync_consumer.py` sketch -- its actual job
      grew beyond eligibility, see below), using `confluent-kafka`.
      Scope expanded during design discussion: the consumer owns **all**
      Postgres -> Pinecone sync for campaigns (add/update/delete), not
      just eligibility. A consumer that only ever deletes on ineligibility
      still left the synchronous app-layer `index_campaign()` calls as a
      second thing writing to Pinecone on approval -- the exact dual-write
      hazard CDC exists to remove. Those calls are now gone:
      `campaigns/review_jobs.py` and `campaigns/api.py`'s moderate
      endpoint no longer call `index_campaign()` at all; the consumer is
      the only thing that writes ad vectors into Pinecone.
- [x] `make kafka-consumer` target added (native host process, matching
      how `backend`/`worker` already run -- **not** a docker-compose
      service; see the "why native, not containerized" note below).
- [x] Parses Debezium's envelope shape (`payload.before`/`payload.after`/
      `payload.op`).
- [x] Decision table, derived from `after` alone and checked uniformly for
      every event (snapshot or live):
      ```
      op == "d"                                   -> delete_vector(before.id)
      op == "r" (snapshot) and status == "active"  -> no-op (trust existing indexed state, avoid re-embed cost on every restart)
      status == "active" and (before is None or before.status != "active" or creative field changed) -> re-embed + upsert
      otherwise                                    -> update_metadata({"status": after.status})
      ```
      `before.status != "active"` forces a full re-embed rather than a
      metadata patch even when the creative is unchanged, because an
      inactive campaign never has a Pinecone record at all -- a metadata
      patch can only edit something that already exists, never create it.
      Every action here is idempotent, so at-least-once redelivery
      (`enable.auto.commit=False`, manual commit after processing) is
      safe.
- [x] Malformed-message handling: catch, log, commit past it -- a
      pragmatic stopgap, not the structured dead-letter topic described in
      Phase 5 (still not built).

**Why a native process, not a docker-compose service**: this project's
documented flow runs `backend`/`worker`/`frontend` natively via `make`
against dockerized infra (`localhost`-mapped ports), not via
`docker-up`'s containerized versions of those same services -- and
`docker-up` currently has a latent, pre-existing gap unrelated to this
work (`backend/.env`'s `DATABASE_URL`/`REDIS_URL` use `localhost`, which
doesn't resolve to the `postgres`/`redis` containers from inside another
container). The consumer follows the same native-process convention:
`kafka_bootstrap_servers` defaults to `localhost:9094`, the host-exposed
listener from Phase 1, not `kafka:9092`.

**Operational consequence**: approving a campaign no longer makes it
servable in the same request -- it becomes servable once the consumer
processes the resulting Kafka event (bounded by Debezium+Kafka lag,
measured ~100-500ms live in Phase 1). `make kafka-consumer` now has to be
running for approvals to actually take effect, same as `make worker`
already does for reviews -- no data loss if it's briefly down (the
compacted topic retains at least the latest event per key indefinitely),
just a visible new local-dev requirement.

## Phase 3 -- backfill existing catalog

- [x] Achieved as a side effect of the Phase 2 decision table, not
      separate logic: Debezium's initial snapshot produces `op:"r"` events
      for every existing row, and any snapshot row that's already
      ineligible falls into the same `update_metadata` bucket a live
      ineligibility change would -- correcting stale pre-CDC drift (e.g. a
      long-completed campaign whose Pinecone record still said
      `"status": "active"`) the first time the consumer ever runs, with no
      dedicated backfill code path needed.

## Phase 4 -- update retrieval.py to actually benefit from this

- [x] `_eligible_campaign_ids`'s Postgres check stays as-is, unconditional
      safety net -- Pinecone is a synced copy with real (if usually small)
      propagation lag, never the source of truth itself.
- [x] `retrieval.py`'s Pinecone query now filters on `status: {"$eq":
      "active"}` unconditionally (previously nothing filtered on status at
      query time at all) -- this is what makes fetching exactly `top_k`
      (see below) viable instead of needing to over-fetch.
- [x] **Date-window eligibility (`start_date`/`end_date`) also pushed into
      Pinecone**, corrected from this doc's earlier "never pushed into
      Pinecone at all" framing -- that framing conflated two different
      cases. Status/budget need CDC because they change asynchronously and
      unpredictably (a budget debit, a report, a re-approval -- no way to
      know in advance). Dates are the opposite: set once at campaign
      creation and never mutated again, so there's no staleness risk in
      writing them into Pinecone metadata once and filtering on them
      against `today` (computed fresh at query time) -- no event-driven
      sync needed at all. Debezium already encodes Postgres `date` columns
      as epoch-day integers in the JSON payload, so the consumer passes
      them straight through with no conversion; `retrieval.py` computes
      `(date.today() - date(1970,1,1)).days` to compare in the same units.
      Required a one-time backfill: existing active campaigns' Pinecone
      records predated this metadata shape, so a bare `start_date`/
      `end_date` range filter would have silently excluded them until
      *something* touched their row again. Fixed by changing the
      consumer's "snapshot of an already-active row" case from a pure
      no-op to a cheap `update_metadata` (status + dates, no re-embed) --
      then resetting the consumer group's offset to earliest and replaying
      all 400 topic messages (`kafka-consumer-groups.sh --reset-offsets
      --to-earliest`), confirmed zero errors and lag back to 0. Verified
      both directions live: a temporarily out-of-window end_date correctly
      dropped a campaign from a filtered query, reverting brought it back.
- [x] **`_OVERSAMPLE_FACTOR` removed entirely** (2026-08-01) --
      `retrieve_candidates` now asks Pinecone for exactly `top_k`, no
      multiplier. Got here through two rounds of testing, the first of
      which was misleading:
      - Baseline (quiet system): 10 calls, 0 trimmed by the Postgres
        safety net every time.
      - First stress test: flipped 140 of 288 campaigns (~49% of the
        entire catalog) simultaneously, at `top_k=10` (oversample fetching
        30). Found up to 16 trimmed in a single call -- at face value,
        evidence *for* keeping a large multiplier. This was wrong to
        generalize from: (a) this app has no organic code path that
        changes half the catalog at once -- only a deliberate bulk SQL
        test does that -- and (b) it used `top_k=10`, not the real
        frontend's `top_k=50` (`frontend/src/components/Feed.tsx`'s
        `BATCH_SIZE`), understating the real margin by 5x.
      - Corrected stress test: flipped a realistic 8 of 288 campaigns
        (organic-scale churn) at the real `top_k=50`. Result: **0 trims
        across 18 calls**, even while hammering the endpoint during the
        active drain window.
      - Also measured the oversampling's own cost directly, since keeping
        it "as free insurance" was itself an unverified assumption:
        Pinecone query latency at `top_k` 50/100/150/250 was flat
        (~0.23-0.28s regardless) -- oversampling turned out to be
        low-cost, not the meaningful cost first assumed either. So the
        removal isn't a performance optimization; it's because an
        occasional short batch is fine for a scrolling feed (the frontend
        just shows fewer ads that scroll, the user gets the rest next
        batch) and the large-scale trimming scenario that would matter
        isn't one this app can actually produce.
      - `retrieve_candidates_oversample` logging kept, renamed
        `retrieve_candidates_trim` -- now the ongoing signal for "is this
        actually happening in practice," not a one-time tuning input.

## Phase 5 -- operational basics

- [x] **Consumer-group lag logging** (2026-08-02) -- `pinecone_sync_consumer.py`
      self-reports its own lag every 30s (`_LAG_LOG_INTERVAL_SECONDS`) via
      `_log_lag()`, computed directly from confluent-kafka's
      `get_watermark_offsets()`/`position()` APIs, no external script or
      monitoring stack needed. Fires on a wall-clock timer independent of
      whether messages are arriving, so it doubles as a liveness heartbeat
      during quiet periods, not just a backlog signal -- exactly the
      "is the sync keeping up" gap that caused real confusion earlier in
      this project (mistook a draining backlog for a bug, twice, before
      this existed). Live-verified: logged `lag: 109 -> 62 -> 0` while a
      real backlog drained, then kept logging `lag: 0` every ~30s during
      a genuinely idle period afterward.
- [x] **Dead-letter topic** (2026-08-02) -- new topic
      `ad_recommender.public.campaigns.dlq` (default delete-based
      retention, deliberately *not* compacted like the main topic, since a
      DLQ needs to retain every failure for a while, not just latest-per-key),
      pre-created via `make kafka-register-connector` alongside the main
      topic. On a processing exception, `_send_to_dead_letter()` produces
      the raw original message (key, value, offset, partition, error
      type/message, timestamp) to this topic and blocks on `flush()`
      *before* the original message gets committed -- so if the DLQ write
      itself ever failed, the original is simply retried next poll instead
      of being lost with no trace anywhere. Still a "pragmatic stopgap,"
      not full SQS/RabbitMQ-grade dead-lettering -- inspecting/replaying
      the DLQ topic is a manual `kafka-console-consumer.sh` job, no
      automatic reprocessing tooling built. Live-verified: published one
      deliberately-invalid (non-JSON) message via
      `kafka-console-producer.sh` with a distinctive test key --
      `pinecone_sync_consumer_processing_error` was logged
      (`JSONDecodeError`), the exact original content showed up correctly
      in the DLQ topic, and a real follow-up event right after it was
      processed normally, confirming the bad message didn't wedge the
      partition.

## Phase 6 -- testing

- [ ] Unit test the consumer's event-parsing + eligibility-decision
      function in isolation (pure function, easy with a fake Debezium
      payload).
- [ ] One integration test: run the full local stack, trigger a budget-
      exhaustion via the real API, assert the Pinecone vector for that
      campaign disappears within a few seconds.

## Status

Phase 0 + Phase 1 done and live-verified (2026-07-30), committed
(`621eacd`).

Phase 2, 3, and part of Phase 4 implemented (2026-07-31):

- `backend/app/campaigns/pinecone_sync_consumer.py` written, implementing
  the decision table above.
- `index_campaign()`'s synchronous call sites removed from
  `campaigns/review_jobs.py` and `campaigns/api.py`.
- `retrieval.py` now filters on `status` at Pinecone query time.
- `make kafka-consumer` target added; `kafka_bootstrap_servers` config +
  `confluent-kafka` dependency added.
- Tests updated: the 5 tests in `test_review_jobs.py` and 2 in
  `test_campaigns_api.py` that patched/asserted on `index_campaign()` were
  rewritten to only assert status transitions (all 14 tests in both files
  pass against live Postgres).

**Live-verified end-to-end (2026-07-31)**, all 8 manual verification steps
passed:

- Campaign 217 flipped to `completed` via psql -- consumer patched its
  `status` metadata, vector stayed (never deleted for eligibility).
- Reverted to `active` via psql (no app involvement) -- consumer
  re-embedded and upserted it, fully restoring servability with zero app
  code involved.
- Headline edited while active -- re-embedded (creative field changed).
- `reviewed_by` edited while active -- metadata-only update, no re-embed.
- Consumer restarted multiple times mid-verification -- resumed from its
  committed offset each time, no crashes, no duplicate-write errors.
- Through the real API: submitted a new campaign, confirmed no Pinecone
  record while `pending_review`; approved it via `/campaigns/{id}/moderate`
  -- confirmed the API response came back before anything existed in
  Pinecone, and only after running the consumer did it appear, correctly
  indexed and queryable with `status: "active"`.
- Bonus, unplanned but useful: an earlier `pytest` run's fixtures created
  and deleted several real campaign rows against live Postgres during this
  session, which the consumer picked up and processed identically to
  everything else (`reembedded` on fixture approval, `deleted` on fixture
  teardown) -- zero errors across ~15 extra real events, a good organic
  stress test of the `op:"d"` path this project's own code doesn't
  otherwise exercise.

**Operational lesson learned during verification, worth recording**: each
short (~15-30s) test run of the consumer sometimes appeared to "miss" an
event that, on investigation via `kafka-consumer-groups.sh --describe`,
turned out to just be real, undrained consumer-group lag -- each Pinecone/
OpenAI round-trip takes roughly 1-1.5s, so a burst of several events
backlogged from rapid manual testing needs a correspondingly longer window
to fully catch up. Not a bug; just don't assume "no new log line" means
"nothing left to process" -- check lag directly if a change doesn't show
up as fast as expected.

**Date-window eligibility + observability added (2026-08-01)**, closing out
the rest of Phase 4:

- `pinecone_sync_consumer.py`'s metadata now includes `start_date`/
  `end_date` (`_metadata_for`/`_status_metadata_for`); the "snapshot of an
  already-active row" case changed from a pure no-op to a cheap
  `update_metadata` call, specifically so it backfills dates without a
  full re-embed.
- One-time backfill performed: `kafka-consumer-groups.sh --reset-offsets
  --to-earliest` + a full replay of all 400 topic messages, confirmed zero
  errors and lag back to 0 afterward.
- `retrieval.py`'s Pinecone query now also filters on the date window
  (`start_date`/`end_date` compared against `today`, encoded as
  epoch-days to match Debezium's `io.debezium.time.Date` representation).
  Live-verified both directions: temporarily setting an active campaign's
  `end_date` to yesterday correctly dropped it from a filtered query;
  reverting brought it back.
- `retrieve_candidates` now logs `retrieve_candidates_trim` (Pinecone
  match count, how many got trimmed by the Postgres safety net, whether
  the result came up short of `top_k`) -- added to collect real data
  before touching `_OVERSAMPLE_FACTOR`, rather than guess. That data
  later led to removing it entirely, see the dated entry below.
- 3 tests in `test_retrieval.py` updated (`_ELIGIBILITY_FILTER` fixture
  constant) to match the new unconditional status/date filter shape; full
  non-LLM test suite (68 tests) passes.

**Real measured latency (2026-08-01)**, replacing an earlier wrong guess
in this doc (that a metadata-only Pinecone write was ~50-150ms -- it
isn't; that number came from a `query()` response header, a *read*, and
was wrongly generalized to `update()`/`delete()` writes):

Per-action-type averages, computed from real timestamps across the
400-message date-window backfill replay:

| action | count | avg | min | max |
| --- | --- | --- | --- | --- |
| `snapshot_metadata_refreshed` | 287 | 1.122s | 0.908s | 2.743s |
| `metadata_updated` | 34 | 1.033s | 0.951s | 1.180s |
| `deleted` | 25 | 1.025s | 0.942s | 1.152s |
| `reembedded` | 28 | 1.708s | 1.278s | 4.076s |

Every Pinecone *write* call -- metadata patch, delete, or upsert --
appears to have a roughly 1-second floor in this environment, not the
near-instant cost originally assumed; a re-embed adds ~0.6-0.7s more on
top for the OpenAI call itself. This floor, not OpenAI, is what actually
dominates the common case, since ~93% of events in the backfill never
touched OpenAI at all.

Confirmed with a clean, isolated, steady-state (zero backlog) live test:
created a fresh campaign, approved it via the real `/moderate` endpoint,
polled Pinecone until visible. API response at `t3`; consumer's own log
timestamp for the resulting `reembedded` event was **2.08s** after `t3`
(polling detected it at 2.54s, the extra ~0.46s being poll-interval
granularity, not real additional lag) -- consistent with the backfill's
`reembedded` average (1.708s) plus normal WAL/Kafka transit time.

**Bottom line**: a change requiring re-embed (an approval, a creative
edit) takes roughly **2-2.5s** steady-state. A change that doesn't (a
status flip, a budget debit, an unrelated field edit -- the ~93% common
case) should be roughly **1-1.5s**, since it skips the OpenAI call. Both
numbers assume the consumer is caught up (lag=0) -- see the next note for
what happens when it isn't.

**Second operational lesson, more important than the first**: mid-session,
`kafka-consumer-groups.sh --describe` showed **732** total messages
backlogged, which looked alarming until traced to its actual cause: this
project's tests run against the real dev Postgres (not an isolated test
database), so every `pytest` run's fixture-created/deleted campaign rows
generate real WAL activity that Debezium faithfully captures into Kafka --
and the consumer had only ever been run in short manual bursts (15-60s at
a time) rather than as a standing background process, so backlog from
several `pytest` runs accumulated unnoticed in between. Nothing was lost
or wrong (that's exactly what a durable log is supposed to do), but it
produced a misleadingly huge latency reading the first time a live test
happened to land behind that backlog. **The consumer should run
continuously during any dev/test session involving this table**, the same
way `make worker` does -- not spun up only when specifically checking
something -- to avoid both the confusion and the backlog itself.

**Performance fix (2026-08-01)**: `vector_store.py`'s `get_index()` was
constructing a fresh Pinecone `Index()` object on every single call instead
of reusing one, unlike `_get_client()` right above it -- not a
request-scoping need (both the FastAPI serving path and this consumer
already run as single long-lived processes), just an inconsistency from
when the file was first written. Added `@lru_cache`, matching
`_get_client()`. Measured directly, twice: `update_metadata` calls went
from ~1.1s each to ~0.3s each (first call always pays one-time connection
setup regardless). This benefits every Pinecone operation in the app, not
just the consumer.

Retesting end-to-end live afterward showed a more complete picture worth
recording: the per-event Pinecone-write time really did drop ~3-4x as
measured, but a live approve-flow test's *total* latency didn't improve
proportionally, because that particular run was dominated by WAL ->
Debezium -> Kafka delivery time, not consumer processing -- a separate
part of the pipeline this fix doesn't touch, and one that appears more
variable under system load than the single ~100-500ms figure from the
original Phase 1 measurement suggested. Doesn't change the correctness
conclusion (Postgres stays the real-time safety net regardless of Pinecone
lag), just means "how long until Pinecone catches up" has two largely
independent contributors, not one.

**Load test validating the fix at volume (2026-08-01)**: the numbers above
came from small samples (n=25-88, mostly incidental) or single live runs
that mixed in WAL/Kafka transit noise. To get a statistically credible
before/after, ran 300 synthetic campaigns through the full lifecycle --
bulk create (`pending_review`) -> bulk approve (`active`, forces re-embed)
-> bulk complete (`completed`, metadata-only) -> bulk delete -- via direct
SQL against live Postgres, watching the real consumer process the
resulting ~1,200+ Kafka events (each delete also emits a compaction
tombstone).

Result: **zero processing errors** across the entire run. A 20-campaign
random sample mid-test showed Pinecone's `status` metadata matching
Postgres exactly in every case. Final state: Postgres and Pinecone's `ads`
namespace both landed back at exactly 288 records (the original catalog),
no orphans, no drift.

Per-action latency, now with real sample sizes:

| action | n | avg | p50 | p90 | p99 |
| --- | --- | --- | --- | --- | --- |
| `metadata_updated` | 319 | 0.427s | 0.359s | 0.421s | 0.491s |
| `deleted` | 26 | 0.348s | 0.334s | 0.390s | 0.433s |
| `reembedded` | 326 | 0.942s | 0.934s | 1.104s | 1.270s |

Compared directly against the pre-fix numbers in the table above
(`metadata_updated` 1.033s avg, `deleted` 1.025s avg, `reembedded` 1.708s
avg): the `get_index()` caching fix delivers a consistent **~2.4-2.9x
speedup on non-embedding writes** and **~1.8x on re-embeds** (a smaller
multiple there since the ~0.6-0.7s OpenAI call is a fixed cost the fix
doesn't touch), holding steady across a sustained run of hundreds of
consecutive calls -- p99 stays close to the mean in all three cases, no
sign of degradation, rate-limiting, or connection exhaustion under load.

**`_OVERSAMPLE_FACTOR` removed (2026-08-01)** -- see Phase 4 above for the
full story, including a stress test that initially argued the wrong way
(tested an unrealistic 49%-of-catalog churn burst at the wrong `top_k`)
before a corrected, realistic test showed the actual risk to be
negligible. `retrieve_candidates` now fetches exactly `top_k` from
Pinecone; an occasional short batch is an accepted, harmless outcome for
a scrolling feed, not something to over-fetch against on every call.

**Phase 5 done (2026-08-02)**: consumer-group lag self-logging (30s
heartbeat, also serves as a liveness signal) and dead-letter handling
(failed messages preserved in `ad_recommender.public.campaigns.dlq`
before being committed past, not just logged and discarded). Both
live-verified -- see Phase 5 above for full detail. `handle_event`'s
decision logic is unchanged; this only touched `run()`'s surrounding
loop. Full non-LLM test suite (68 tests) still passes.

Not yet committed to git (this Phase 2 slice) -- pending user go-ahead,
same as Phase 0/1 was before it.
