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

No longer just an idea — actively planned, see `docs/next_phase_plan.md`
for the full design (four views: onboarding chat + feed, performance
dashboard, campaign submission, moderator queue).
