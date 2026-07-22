# Next phase: fix the profile-learning loop, build the frontend

Captures everything decided in the planning conversation before starting
this phase, so it doesn't need to be re-derived or re-litigated. Nothing in
this file is built yet — see the TODO list at the end for status as work
starts.

## Why this phase

`docs/spec.md`/`README.md` describe the backend as fully built, but the
"profile learns over time and improves future recommendations" story was
never actually true: `retrieve_candidates` re-embeds whatever raw text is
passed to it on every call, and never reads back the profile vector
`record_feedback` nudges and stores. Also, there is still no frontend
beyond the Vite placeholder — this phase covers both.

## Decisions

### Retrieval bug fix
`retrieve_candidates` should try `fetch_vector(user_id, namespace="users")`
first; only fall back to embedding provided text if no profile exists yet
(a brand-new user, cold start).

### User profiles without login
No accounts, no auth (consistent with the existing no-auth scope
decision). A "user" is just a `user_id` string the caller supplies — same
attribution-not-authentication pattern already used for `advertiser_name`
and moderator `reviewed_by`.
- `POST /users` — `{user_id, interest_summary}`: embeds the summary, stores
  it as the starting profile vector in Pinecone's `users` namespace.
- `GET /users/{user_id}` — fetch a profile's current state for display.

### View 1a — onboarding chat
A new agent, but a simple one: plain multi-turn conversation, **no tools
needed**, so per the project's LangChain-vs-raw-SDK rule this uses the raw
OpenAI SDK directly, not LangChain.
- Two calls per turn: **(1)** a streamed plain-text reply (the visible,
  natural conversation — asks exploratory questions about the user's
  interests); **(2)** a separate, non-streamed, quick structured-output
  call after each exchange that looks at the full conversation and decides
  `{ready_to_finish: bool, interest_summary: str | None}`. Structured
  output doesn't stream as readable text, so it's kept out of the
  user-visible call entirely.
- Once `ready_to_finish`, frontend calls `POST /users` with a freshly
  generated `user_id` + the synthesized `interest_summary`, then swaps to
  the feed.
- Streaming mechanism: FastAPI `StreamingResponse`, consumed on the
  frontend via `fetch` + a `ReadableStream` reader — not the browser's
  `EventSource`, since `EventSource` only supports GET and this needs to
  POST the conversation history. Not WebSocket — that's for genuinely
  bidirectional continuously-open connections; this is one-directional
  (server to client), repeated per turn.
- Chat history is ephemeral (kept client-side only, not persisted
  server-side) — only the final `interest_summary` gets persisted, via
  `POST /users`.
- Restart/reset button: abandon the current `user_id` client-side, start a
  fresh chat with a new one. No deletion needed.

### View 1b — the feed
Threads-like vertically scrolling list; each item is one served ad.

**Batching, not one call per item.** Calling `/recommend` once per scroll
item would mean paying for the expensive part (LLM re-ranking) on every
single item — bad latency, cost scales with scroll depth. Instead: a
batch-recommend call returns a ranked batch of N eligible ads in one call
(one embed, one Pinecone query, one LLM re-rank covering all N, one
guardrail pass). Frontend displays them one at a time while scrolling,
prefetching the next batch once the current one runs low (standard
infinite-scroll pagination) — not fetching per item.

**Impressions are client-triggered**, not logged inside the batch-fetch
call (fetching a batch ≠ having seen every item in it). Use an Intersection
Observer to detect when an item actually scrolls into view, then fire a
small, cheap "log impression" call (DB insert only, no LLM call) — same
pattern real feeds (Twitter, Instagram) use.

**Reactions** (replaces the earlier click/no_click/conversion idea, which
turned out confusing — this is the resolved version):
- **Like** ❤️ — mild positive signal (small profile nudge, small budget
  cost).
- **Interested** ⭐ — the strong positive signal; this is "conversion" in
  the schema/DB/dashboard (correct ad-tech term for the CPA-relevant
  metric), labeled "Interested" in the UI. Same reaction-bar mechanic as
  Like/Dislike, not a separate visually-distinct CTA button (that was the
  earlier, more confusing design — replaced, not layered on top of).
- **Dislike** 👎 — explicit negative signal, stronger than silence; a
  deliberate rejection is a much stronger signal than someone just not
  reacting.
- **No reaction at all** — still possible and common; no event fires, no
  nudge happens.
- **Report** — a predefined category list (misleading / offensive /
  irrelevant / spam / other) plus optional free text when "other" is
  picked. Increments a `report_count` on the campaign; crossing a
  threshold (default: 3, should be easy to adjust) auto-flips the campaign
  to `needs_review`. Simple threshold version for this phase — see
  `docs/future_ideas.md` for the richer escalation-agent version this
  motivates (an agent judging whether/how to escalate based on the report
  pattern, rather than a hardcoded number).
- **Do Not Show Again** — not a learning signal, a permanent per-user
  exclusion. Separate action: add the ad_id to a per-user blocklist (stored
  in the user's Pinecone metadata), checked and filtered during retrieval.
- **Why Am I Seeing This** — no new backend call. `/recommend` (batch)
  already returns the full decision trace (candidates, LLM justification,
  guardrail result) per item; the frontend just needs to hold onto it per
  feed item instead of discarding it, and show it on demand.

### View 2 — performance dashboard
Aggregate across all demo activity, not per-user (a single demo profile
won't generate enough events for a meaningful trend; this project is
framed as a dashboard *into the engine*, not into one person's feed).

Needs a real event log — `record_feedback` currently only keeps the *last*
outcome (overwrites each time), no history to chart. New Postgres table
logging every impression/like/dislike/interested(conversion)/report with a
timestamp, written by the impression endpoint and the reaction endpoint(s).

Metrics (designed properly, not just one CTR line):
- **CTR** = conversions ("Interested" taps) ÷ impressions — the real
  click-through-rate equivalent, now that "like" isn't playing double duty
  as both a click and a reaction.
- **Engagement rate** = likes ÷ impressions.
- **Dislike rate** = dislikes ÷ impressions — a campaign quality signal,
  distinct from "report" (which signals a policy problem specifically).
- Total spend, average CPA (spend ÷ conversions).
- Rolling CTR trend line over time — the "is it learning" chart from the
  original spec, still the centerpiece.
- Per-campaign breakdown table (impressions/likes/dislikes/conversions/
  CTR/spend/report_count per campaign) — standard in real ad dashboards
  (Google Ads, Meta Ads Manager), and also how a report problem would
  surface to whoever's running the system.

New endpoint aggregating from the event log + campaigns table. Use the
`dataviz` skill when actually building the chart components.

### View 3 — submit campaign + status table
Single implicit advertiser for now, no advertiser picker/login. Backend
already fully supports this (`POST /campaigns`, `GET /campaigns`) — this
view is UI-only, no new backend.

### View 4 — moderator queue
Also already fully backend-supported (`GET /campaigns?status=needs_review`,
`POST /campaigns/{id}/moderate`) — UI-only. New tie-in from this phase:
reports piling up on a campaign (View 1b) can land it in this same queue.

## TODO

Backend:
- [ ] Fix `retrieve_candidates` to read the stored profile vector first,
      fall back to embedding text only for a brand-new user
- [ ] `POST /users`, `GET /users/{user_id}`
- [ ] Event log table (impression/like/dislike/interested/report) +
      migration
- [ ] Batch-recommend endpoint (ranked list of N, not just the top one)
- [ ] Impression-logging endpoint (lightweight, client-triggered)
- [ ] Reaction endpoint(s): like/dislike/interested/report (with
      category+reason), do-not-show-again (separate, not a learning
      signal)
- [ ] Per-user blocklist storage + retrieval filtering
- [ ] Report-count threshold auto-flip to `needs_review`
- [ ] Performance aggregation endpoint (CTR/engagement/dislike rate,
      spend, CPA, rolling trend, per-campaign breakdown)
- [ ] Streaming onboarding-chat endpoint (two calls per turn, as above)
- [ ] Update `LEARNING_RATE`/`COST_PER_OUTCOME` dicts and `FeedbackEvent`
      schema for the new outcome vocabulary (like/dislike/conversion,
      `no_click` becomes purely implicit, never sent explicitly)
- [ ] Rework or retire `scripts/simulate_feedback_rounds.py` to match the
      new outcome vocabulary (currently sends explicit click/no_click)
- [ ] Update `docs/future_ideas.md`: note report-count as the concrete
      trigger for the escalation-agent idea

Frontend (`frontend/`, currently just the Vite placeholder):
- [ ] View 1a — onboarding chat (streamed)
- [ ] View 1b — feed (batched fetch, Intersection-Observer impressions,
      Like/Dislike/Interested reactions, report modal, do-not-show-again,
      why-am-I-seeing-this)
- [ ] View 2 — performance dashboard
- [ ] View 3 — submit campaign + status table
- [ ] View 4 — moderator queue
