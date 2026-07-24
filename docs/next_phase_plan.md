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
`retrieve_candidates` now takes a `user_id` and requires
`fetch_vector(user_id, namespace="users")` to return a real vector -- no
fallback to embedding raw text. Onboarding (`POST /users`, then the
checkpoint flow) always creates a profile before a user ever reaches
`/recommend`, so a missing profile means recommend was called before
onboarding finished -- a bug to surface (raises `ValueError` -> HTTP 404),
not a legitimate cold-start case to paper over silently. **Done.**

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
OpenAI SDK directly, not LangChain. Not just Q&A, though -- it uses
retrieval and real reactions to build the profile, not only conversation
text (see "Why this is RAG" below for the reasoning behind that).

**Per turn, two calls, checkpoint first**: (1) `POST /onboarding/checkpoint`
-- non-streamed structured output, `{show_candidates, ready_to_finish,
interest_summary}` plus `candidates` if `show_candidates` fired -- then
(2) `POST /onboarding/chat` -- the streamed, user-visible reply, told just
`show_candidates: bool` for this turn (not the candidate details) so it can
naturally acknowledge showing something without needing specifics.
Checkpoint runs first specifically so chat can know whether this turn
includes candidates.

`show_candidates` is a separate gate from `ready_to_finish`: a single vague
reply ("need to fix some things") shouldn't trigger a real checkpoint
(embed + seed + Pinecone retrieval) just because a turn happened -- only a
concrete, specific interest signal should. `ready_to_finish` can only be
true once `show_candidates` has fired (this turn or an earlier one) --
onboarding can't finish without ever having shown/tested a candidate.
`interest_summary` is always populated, even a rough best-effort one.

**The full flow (implemented)**:
1. Turn 1: chat opens with a static, hardcoded question (no API call --
   no point spending a call on a canned greeting). User replies.
2. **Checkpoint**: judge `show_candidates`/`ready_to_finish`/
   `interest_summary` from the conversation so far. If `show_candidates`
   is false (signal still too vague), skip straight to step 5 -- no
   embed/seed/retrieve call happens, the turn is just conversation.
3. Once `show_candidates` fires: seed the profile (first time only --
   embed `interest_summary`, equivalent to `POST /users`) and call the
   existing `retrieve_candidates` for a few real candidates. Chat is told
   `show_candidates=true` for this turn so its streamed reply can
   naturally acknowledge that candidates are coming.
4. Candidates are shown as reactable cards. Reactions go through the
   *existing* `POST /events/reaction` (nudges the profile via
   `record_feedback`, debits budget, logs the event -- no separate
   onboarding-specific reaction endpoint). The client then folds the
   reactions into an ordinary **user** message for the next turn (e.g.
   "(I liked the plumbing ad, wasn't interested in the hardware store
   one.)") -- real user-originated signal, just translated from taps to
   text, not a synthetic message role or a server-side event-table
   lookup (both considered and rejected as more machinery than needed).
   This is what feeds retrieved content back into the next generation and
   is what makes this RAG, not just retrieval (see below).
5. If `ready_to_finish` comes back true, wrap up. Otherwise keep chatting
   and re-checking each turn. **Capped at 3 real checkpoint rounds total**
   (client-enforced, counting only turns where `show_candidates` fired) --
   if still ambiguous after the third, finalize with whatever profile
   exists rather than dragging on indefinitely.
6. Once finished, transition to the feed. The profile already exists and
   is reaction-tested from the checkpoints -- no separate "finalize"
   API call needed.

**Why this is RAG, not just retrieval** (worth keeping the reasoning, not
just the conclusion -- this was non-obvious): showing retrieved candidates
to a human to react to is *not* RAG by itself, that's just retrieval plus
human input, no generation is informed by what's retrieved. The round-trip
through the user (retrieve -> show -> user reacts -> reaction comes back)
doesn't disqualify it either -- real interactive RAG systems commonly have
a human step in the middle (e.g. a shopping assistant that shows retrieved
products, gets a reaction, and reasons about that reaction using the
products' actual content in its next response). What makes it RAG here is
step 4 specifically: the retrieved items' real content -- not just an
opaque "liked/disliked" signal -- sits in the context that produces the
next generation. Skip step 4 and this stops being RAG; it'd just be
retrieval with an extra UI step.

**Other mechanics**:
- Streaming: FastAPI `StreamingResponse` wrapping a generator that filters
  OpenAI's `response.output_text.delta` events out of the raw stream --
  consumed via `fetch` + a `ReadableStream` reader on the frontend -- not
  `EventSource` (GET-only, and this needs to POST conversation history),
  not WebSocket (that's for genuinely bidirectional continuously-open
  connections; this is one-directional, server to client, repeated per
  turn). Deliberately not wrapped in `@retry` like other LLM calls in this
  project -- a mid-stream failure can't be usefully retried the way a
  single blocking call can.
- Chat history is ephemeral (client-side only, never persisted
  server-side) -- only the profile vector built during checkpoints
  persists, via the same Pinecone upserts `record_feedback` already does.
- Restart/reset button: abandon the current `user_id` client-side, start a
  fresh chat with a new one. No deletion needed.
- The chat UI needs to render ad-card-like elements with reaction buttons
  inline in the conversation, not just text bubbles -- meaningfully more
  frontend work than a plain chat. Depends on a reasonable, varied
  candidate pool already existing for checkpoints to draw from (see
  seeding, below) -- onboarding can't show good candidates if the campaign
  catalog is thin.

### Demo data seeding

A script to populate a reasonable, varied campaign catalog before the
frontend work is demoable at all -- onboarding checkpoints and the feed
both need real candidates to draw from.

**Cost-conscious design (the project has a small real API budget, this
was checked, not assumed)**: create real `Advertiser` + `Campaign` rows for
each seed campaign (proper ownership, budget, eligibility dates -- the
full data model, unlike the deleted `load_catalog.py`, which had none of
that structure at all), but set `status="active"` directly and call
`index_campaign` (embed + Pinecone upsert) directly -- **skip the LLM
policy-review call entirely** for seed data, since we're writing the
content ourselves and already know it's compliant, and a real review call
(chat completion, sometimes triggering web search) is the actually
expensive part, not the embedding. Confirmed: `text-embedding-3-small` is
$0.02 per 1M tokens (input only, no output tokens for embeddings) -- even
100 seed campaigns at a generous 100 tokens each is 10,000 tokens, i.e.
$0.0002 total. The real review pipeline still gets fully exercised
whenever someone actually submits a new campaign live through View 3 --
that's one on-demand call at normal usage, not a bulk cost.

Include some variety deliberately: different categories, and some of the
same substantiation/restricted-category cases already used to test policy
review (alcohol/gambling needing exclusions), so the catalog is realistic
even though these particular ones skip live review. **Implemented as ~288
campaigns across 18 categories** (16 each) -- generation (an LLM call per
category) and loading (Postgres + Pinecone writes) are split into two
scripts, `generate_seed_campaign_data.py` and `seed_demo_campaigns.py`, so
the generated content is a versioned JSON fixture (`data/seed_campaigns.json`)
rather than something re-generated (non-deterministically, and at some
small cost) on every re-seed of a reset dev environment.

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

**Already-shown suppression (added during implementation, not in the
original plan)**: `retrieve_candidates` excludes any campaign with an
impression logged for the user in the last hour, so the same ad doesn't
keep reappearing during a scroll session. Time-boxed, not permanent -- a
permanent exclusion would eventually exhaust the demo's finite catalog.
Implemented as a Pinecone query-time `$nin` filter (on the `campaign_id`
metadata field, alongside the existing do-not-show blocklist), not a
Python post-filter on an already-fetched batch -- Pinecone's single-stage
filtering searches past excluded IDs for real matches during the search
itself, verified live (blocklisting the top 2 of a 5-item query still
returned a full 5, not 3).

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
- [x] Fix `retrieve_candidates` to read the stored profile vector first
      (raises if missing, no silent fallback -- see above)
- [x] `POST /users`, `GET /users/{user_id}` (profile lives in Pinecone's
      `users` namespace only, no Postgres table)
- [x] Event log table (impression/like/dislike/interested/report) +
      migration
- [x] Batch-recommend endpoint (ranked list of N, not just the top one) --
      `POST /recommend/batch`, also extracted /recommend and /feedback out
      of main.py into app/serving/api.py
- [x] Impression-logging endpoint (lightweight, client-triggered) --
      `POST /events/impression`
- [x] Reaction endpoint(s): like/dislike/interested/report (with
      category+reason), do-not-show-again (separate, not a learning
      signal) -- `POST /events/reaction`, `POST /events/report`,
      `POST /users/{user_id}/do-not-show`; retires `/feedback` and
      `FeedbackEvent` entirely rather than keeping both
- [x] Per-user blocklist storage + retrieval filtering -- blocklist lives
      in the user's Pinecone metadata, filtered in `retrieve_candidates`
- [x] Report-count threshold auto-flip to `needs_review` -- counted
      straight from the `events` table (`REPORT_THRESHOLD = 3`), no
      separate counter column; escalation agent left as a future upgrade
      per `docs/future_ideas.md` (confirmed, not pulled into this phase)
- [x] Performance aggregation endpoint (CTR/engagement/dislike rate,
      spend, CPA, rolling trend, per-campaign breakdown) -- `GET /performance`
- [x] Streaming onboarding-chat endpoint (two calls per turn, checkpoint
      first) -- `POST /onboarding/chat`, `POST /onboarding/checkpoint`
- [x] Onboarding checkpoint logic: `show_candidates` gate (separate from
      `ready_to_finish`) so a vague reply doesn't trigger a real checkpoint,
      seed-once + retrieve when it fires, cap at 3 real rounds
      (client-enforced); reactions folded into ordinary user messages
      rather than a synthetic role -- verified live end-to-end
- [x] Demo data seeding script (real Advertiser/Campaign rows, skip LLM
      review, direct embed + index) -- split into
      `generate_seed_campaign_data.py` (LLM generation -> versioned
      `data/seed_campaigns.json`) and `seed_demo_campaigns.py` (loads that
      file into Postgres/Pinecone, no LLM call); ~288 campaigns across 18
      categories, verified end-to-end via a live `/recommend/batch` call
- [x] Update `LEARNING_RATE`/`COST_PER_OUTCOME` dicts for the new outcome
      vocabulary (like/dislike/interested; `no_click` is now purely
      implicit -- no event fires at all for silence, never sent
      explicitly)
- [x] Rework `scripts/simulate_feedback_rounds.py` to match the new
      outcome vocabulary (now simulates a like reaction, not click/no_click)
- [x] Update `docs/future_ideas.md`: report-count noted as the concrete
      trigger for the escalation-agent idea (done in an earlier planning
      pass, before implementation started)

Frontend (`frontend/`, currently just the Vite placeholder):
- [ ] View 1a — onboarding chat (streamed, with reactable candidate cards
      at each checkpoint)
- [ ] View 1b — feed (batched fetch, Intersection-Observer impressions,
      Like/Dislike/Interested reactions, report modal, do-not-show-again,
      why-am-I-seeing-this)
- [ ] View 2 — performance dashboard
- [ ] View 3 — submit campaign + status table
- [ ] View 4 — moderator queue
