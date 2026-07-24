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
