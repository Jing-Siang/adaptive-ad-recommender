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

## Frontend dashboard

Recommendation dashboard, decision-trace viewer, CTR chart, and a
moderator-queue page. Tracked as a known gap in README's Status section —
listed here too since it's the same category of "designed for, not yet
built."
