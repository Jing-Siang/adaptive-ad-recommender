# Future ideas

Not scheduled, not designed in detail — captured here so they don't get lost
or silently re-litigated later.

## Escalation agent (serving side)

After `POST /recommend` serves a decision, an agent reviews the decision
trace (candidates, guardrail results, what was actually served) and decides
whether something is worth flagging to a human — e.g. a high-relevance ad
got guardrail-blocked, or every candidate in a round got blocked. If so, it
calls a notification tool (Slack, or a structured log entry as a simpler
stand-in) to alert someone.

This is a genuine fit for an actual agent loop (`create_agent` or
equivalent), unlike the policy reviewer's `web_search` usage: sending a
Slack message isn't a hosted/server-side tool — it's custom code our own
process has to execute, which is the one case in this project that would
actually need agent-loop machinery rather than a single provider API call.

Open questions before building this: what specifically counts as
"escalation-worthy" (a judgment call, similar to tuning the policy-review
prompt), and whether to wire up a real Slack webhook or start with a
logged stand-in.

**Concrete trigger identified (2026-07-23)**: the feed's "report" reaction
(see `docs/next_phase_plan.md`) gives this a real, motivated starting
point. That phase ships a simple version first -- a hardcoded report-count
threshold auto-flips a campaign to `needs_review`. The natural upgrade is
replacing that hardcoded number with this agent: it would look at the
report pattern (count, rate vs. impressions, maybe guardrail-block
history) and decide whether/how to escalate, with a reason -- a genuine
"custom tool the model decides to call" case (the tool being "flip this
campaign to needs_review", which our code has to actually execute).

## Frontend dashboard

No longer just an idea — built, see `docs/next_phase_plan.md` for the full
design (four views: onboarding chat + feed, performance dashboard, campaign
submission, moderator queue).

## Push date-window eligibility into Pinecone's query filter

**Done** (2026-08-01) -- superseded by `docs/kafka_cdc_plan.md`. The
original framing below turned out to be only half right: `status` and
`budget_spent` *did* end up moving into Pinecone too, not just
`start_date`/`end_date` -- Kafka + Debezium CDC (`pinecone_sync_consumer.py`)
syncs all of it from Postgres in near-real-time, which avoids the
dual-write staleness bug this note originally worried about (Postgres
stays the only thing anyone writes to directly; Pinecone converges from
the CDC log, not from a second manual write). `_eligible_campaign_ids`'s
Postgres check and the oversample factor both still exist as the safety
net for the small residual sync lag -- see that doc for the full design,
live verification, and load-test numbers. Original note, kept for
history:

`serving/retrieval.py`'s `_eligible_campaign_ids` checks three things against
Postgres: `status == "active"`, `budget_spent < budget_total`, and the
`start_date`/`end_date` window. The oversample factor (`_OVERSAMPLE_FACTOR`)
exists to compensate for whatever this Postgres-side post-filter trims.

The date-window piece could move into Pinecone's own query-time filter
(alongside the existing blocklist/recently-shown `$nin` filter) without any
staleness risk: `start_date`/`end_date` are immutable once a campaign is
created, so storing them as numeric timestamps in the ad's metadata and
filtering `{"start_date_ts": {"$lte": today_ts}, "end_date_ts": {"$gte":
end_date_ts}}` at query time is always accurate -- "today" is computed fresh
in Python per query, not written back to Pinecone, so there's nothing to
resync.

`status` and `budget_spent` can't make the same move: both change *after*
indexing (a budget debit or status flip happens in Postgres via
`feedback.py`/`review_jobs.py`, with nothing syncing that back to Pinecone's
metadata). Filtering on a Pinecone-stored status/budget would reintroduce
exactly the dual-write staleness bug the "Postgres is the only source of
truth for status/budget" rule (see `docs/spec.md`) was designed to prevent
-- a campaign that just got budget-exhausted would keep serving until
someone remembered to push that update to Pinecone too.

So this is a partial win, not a full replacement of `_eligible_campaign_ids`
or the oversample factor -- status/budget still need the Postgres check
(and some oversampling to compensate for it). Requires adding new metadata
fields and re-indexing the entire existing seed catalog to backfill them.
Not pursued now because the actual cost concern that prompted this
discussion (2026-07-24) was already negligible either way -- Pinecone query
cost doesn't meaningfully change with `top_k`, oversampled or not, at this
catalog's scale.

## Async/batched budget debits, if `campaigns.budget_spent` ever becomes a hot row

Raised (2026-08-02) while designing the reaction-idempotency fix (see the
`reactions` table work): does the per-reaction, synchronous
`UPDATE campaigns SET budget_spent = budget_spent + delta` scale to "many
many users"?

Short answer: the per-user pieces (the `reactions` table, one row per
`(user_id, campaign_id)`, and each user's own Pinecone profile vector) scale
fine on their own -- different users touch different rows/keys, so there's
no contention between them, and the standard horizontal-scaling playbook
(more backend replicas, Postgres handles many independent indexed
row-ops/sec, Pinecone already scales itself) covers "many many users"
without needing anything new.

The one real hot spot is `campaigns.budget_spent` itself: every user
reacting to the *same* campaign contends for that *one* row. This is the
same shape as "Instagram's like counter on a viral post" -- not a users
problem, a single-hot-campaign problem, and only shows up if one campaign
gets a very large burst of concurrent reactions. The current atomic
`UPDATE ... SET x = x + delta` is already the right first-line answer (it's
what you reach for before you need anything fancier), and comfortably
handles far more load than this project will ever see -- not a real
problem today, deliberately not building around it yet.

If it ever became one, the industry pattern (this is genuinely how
Instagram/Twitter-scale systems handle a hot counter) is to stop updating
the shared row synchronously in the request path at all: publish a
"reaction happened" event to a durable log (Kafka, in our case -- we
already have the infra) instead, and have a separate consumer batch many
events together over a short window and apply one combined increment,
dramatically cutting how often that one row actually gets locked. At more
extreme scale, the counter itself gets sharded into several sub-rows that
absorb writes independently and get summed at read time, with the
displayed number allowed to be slightly eventually-consistent -- the same
trade this project already made for Postgres -> Pinecone sync. This would
be architecturally very similar to the existing CDC consumer
(`pinecone_sync_consumer.py`): same "Kafka absorbs the write, a consumer
applies it asynchronously" shape, just aimed at a different hot spot.
Not scheduled -- there's no evidence this project needs it, and building
it now would be solving a scaling problem that doesn't exist yet.

**Redis vs. Kafka for this, and why they'd likely end up combined
(2026-08-12).** Redis's `INCRBYFLOAT` is also a real, standard pattern for
exactly this kind of pre-aggregation buffer -- picking Kafka above wasn't
"Redis loses," it's two tools that are actually good at opposite halves of
the problem:

- *Durability*: Redis holds the counter in memory; even with persistence
  turned on (RDB snapshots, or AOF with `appendfsync everysec`), a crash at
  the wrong moment can lose the last fraction of a second's writes --
  for a budget counter, that's a reaction that happened but never gets
  counted, silently. Kafka is built specifically not to do this: a producer
  can require its write be durably persisted (and replicated, with >1
  broker) before it's acknowledged, and a crashed consumer just resumes
  from its last committed offset and reprocesses -- nothing before that
  point can vanish. For something tracking money, this matters more than
  which one happens to already be running.
- *Live reads*: the flip side favors Redis. If eligibility needs to check
  "budget spent so far, including whatever hasn't been flushed to Postgres
  yet," Redis's `GET` answers instantly by design. Kafka has no equivalent
  -- there's no cheap way to "peek" at how much is sitting unconsumed in a
  topic for one specific campaign without actually consuming it, which is
  awkward for a per-request live check.

So a real system wanting both durability and fast reads would likely run
them together: Kafka as the durable source of truth for "this reaction
happened," with a Redis counter layered on top purely as a disposable,
fast read-cache of the current pending total -- rebuildable at any time by
replaying the Kafka log if Redis ever lost it. That's the same "one
durable source of truth, one derived fast-access layer" shape as
Postgres+Pinecone elsewhere in this project, just with Kafka playing the
source-of-truth role instead of Postgres. Still not scheduled, same
reasoning as above -- this is the refined shape of the idea, not new
urgency to build it.

## Profile-vector nudge has an unprotected read-then-write race

**Fixed (2026-08-16)** -- `record_feedback()` now holds a session-level
Postgres advisory lock (`pg_advisory_lock(hashtext(user_id))`) around the
profile-vector fetch and write, so a second concurrent reaction from the
same user can't fetch the same stale vector before the first one's write
lands. Verified with a real two-thread test
(`test_record_feedback_serializes_profile_vector_updates_for_same_user`)
that fails without the lock and passes with it -- confirmed by temporarily
removing the lock and watching the test catch the exact race. Original
note, kept for history:

Found (2026-08-12) while discussing request-volume scaling, separate from
the reaction-idempotency fix (`reactions` table, atomic upsert). That fix
protects the *reaction* row for a given `(user_id, campaign_id)`. It does
nothing for the *profile vector* itself, which `record_feedback()`
(`app/serving/feedback.py`) still updates as three unguarded steps:

```python
profile_vector = fetch_vector(user_id, namespace="users")   # read
new_vector = update_profile_vector(profile_vector, ad_vector, rate_delta)
update_vector(user_id, new_vector, namespace="users")        # write
```

If the same user has two reactions genuinely in flight at once (e.g.
reacting to ad A then ad B within the same round-trip window), both
requests can fetch the same starting vector before either writes back --
whichever `update_vector()` lands last wins outright, silently overwriting
the other's nudge. Not a cross-user problem (different users' vectors
never contend, see below); this is the same user racing against
themselves.

Why it can't be fixed the same way as the `reactions` table: Postgres has
a real atomic primitive for "read the old value and write the new one as
one indivisible step" (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`).
Pinecone has no equivalent -- `update_vector()` just overwrites `values`
wholesale with whatever's passed in, so there's no server-side "nudge this
vector by X, atomically" operation to reach for. A real fix would need
application-level coordination (e.g. a lock per `user_id` serializing
concurrent profile updates for that user), which is new machinery, not a
one-line change.

Lower severity than the budget race that motivated the `reactions` table
work: nothing financial is at risk, and it's self-correcting -- a lost
nudge just means that one reaction's signal doesn't make it into the
profile this round, not a compounding error. Not pursued now for that
reason; documenting so it isn't mistaken for "already handled" just
because the reaction-row race got fixed.

**Aside, why this doesn't apply across users:** `fetch_vector`/
`update_vector` are keyed by `user_id` -- each user has their own vector,
so this is a one-to-one relationship, unlike `campaigns.budget_spent`
(many users, one shared row). A huge burst of concurrent reactions across
*different* users causes no contention here at all; the race above only
ever involves one user's own overlapping requests.
