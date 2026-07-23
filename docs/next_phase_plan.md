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

**Per turn, two calls**: (1) a streamed plain-text reply -- the visible,
natural conversation, asking exploratory questions -- and (2) a separate,
non-streamed, quick structured-output call that looks at the full
conversation so far and returns `{ready_to_finish: bool, interest_summary:
str}`. `interest_summary` is always populated, even a rough best-effort
one, not only once "finished" -- structured output doesn't stream as
readable text, which is why it's a second call kept out of the
user-visible stream entirely.

**The full flow**:
1. Turn 1: chat opens with a broad question. User replies.
2. **Checkpoint**: embed the current `interest_summary`, pull a few real
   candidates via the existing `retrieve_candidates`, show them in the
   chat as reactable cards ("here's a few things you might like"). The
   very first checkpoint calls `POST /users` to seed the profile from this
   embedding, since no profile exists yet at that point.
3. User reacts (like/dislike) to the shown candidates. These reactions
   *are* real feedback -- nudge the profile using the same
   `update_profile_vector` logic `record_feedback` already uses for the
   feed.
4. Feed the shown candidates + how the user reacted to them back into the
   chat's own context, so the *next generated question* is actually
   informed by that (liked plumbing, disliked skincare -> ask about
   DIY/home-repair specifically, not a generic follow-up).
5. If reactions were confidently positive, wrap up soon. If mixed or
   negative, ask 1-2 more clarifying questions, then repeat from step 2
   with a refreshed `interest_summary`. **Capped at 3 checkpoint rounds
   total** -- if still ambiguous after the third, finalize with whatever
   profile exists rather than dragging on indefinitely.
6. Once finished, transition to the feed. The profile already exists and
   is reaction-tested from the checkpoints -- no separate "finalize"
   API call needed beyond what step 2 already did.

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
- Streaming: FastAPI `StreamingResponse`, consumed via `fetch` + a
  `ReadableStream` reader on the frontend -- not `EventSource` (GET-only,
  and this needs to POST conversation history), not WebSocket (that's for
  genuinely bidirectional continuously-open connections; this is
  one-directional, server to client, repeated per turn).
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
review (alcohol needing exclusions, health/financial claims), so the
catalog is realistic even though these particular ones skip live review.

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
- [x] Fix `retrieve_candidates` to read the stored profile vector first
      (raises if missing, no silent fallback -- see above)
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
- [ ] Onboarding checkpoint logic: rough interest_summary every turn,
      retrieve + return candidates for reactable cards, cap at 3 rounds
- [ ] Demo data seeding script (real Advertiser/Campaign rows, skip LLM
      review, direct embed + index -- see "Demo data seeding" above)
- [ ] Update `LEARNING_RATE`/`COST_PER_OUTCOME` dicts and `FeedbackEvent`
      schema for the new outcome vocabulary (like/dislike/conversion,
      `no_click` becomes purely implicit, never sent explicitly)
- [ ] Rework or retire `scripts/simulate_feedback_rounds.py` to match the
      new outcome vocabulary (currently sends explicit click/no_click)
- [ ] Update `docs/future_ideas.md`: note report-count as the concrete
      trigger for the escalation-agent idea

Frontend (`frontend/`, currently just the Vite placeholder):
- [ ] View 1a — onboarding chat (streamed, with reactable candidate cards
      at each checkpoint)
- [ ] View 1b — feed (batched fetch, Intersection-Observer impressions,
      Like/Dislike/Interested reactions, report modal, do-not-show-again,
      why-am-I-seeing-this)
- [ ] View 2 — performance dashboard
- [ ] View 3 — submit campaign + status table
- [ ] View 4 — moderator queue
