# Ad Policy

Rules a campaign's creative (headline + description + category) must satisfy
before it is eligible to serve.

## 1. Prohibited outright — reject, do not escalate

- Illegal goods or services, counterfeit goods, weapons, or drugs (other than
  legally regulated substances handled under section 2).
- Content that is sexually explicit, promotes violence, or is discriminatory
  toward a protected group.
- Claims that are provably false (e.g. "cures cancer", "guaranteed to double
  your money").

## 2. Restricted categories — require a context exclusion

These categories are allowed, but the campaign's `excluded_categories` must
include the listed contexts, so the ad is never shown alongside that kind of
content:

| Campaign category | Must exclude context(s)              |
|---|---|
| alcohol            | sensitive, health, recovery           |
| gambling            | sensitive, finance_distress            |
| tobacco             | sensitive, health, youth               |

If a campaign in one of these categories is missing the required exclusions,
add them and approve — don't reject solely for a missing exclusion list.

## 3. Substantiation required — escalate to human review if unclear

- Health or medical claims ("relieves pain", "boosts immunity") need
  supporting evidence to approve outright. If the creative doesn't make clear
  whether a claim is substantiated, escalate — don't guess.
- Financial claims (investment returns, "get rich" framing) must not promise
  guaranteed outcomes. Ambiguous wording ("great returns" without a number)
  should escalate rather than be auto-rejected.
- Urgency/scarcity language ("only 3 left", "offer ends today") is allowed
  only if plausibly true for the advertiser's business — escalate if it reads
  as a manufactured/fake deadline tactic and you're not confident either way.

## 4. Always fine — approve without hesitation

- Ordinary product/service ads (home repair, retail, food, electronics,
  local services) with no claims from sections 1–3, and with correct
  category exclusions already in place per section 2 if applicable.

## Decision guidance

- **approved** — clearly satisfies the policy (section 4, or a restricted
  category from section 2 with correct exclusions).
- **rejected** — clearly violates section 1, or an unambiguous unsubstantiated
  claim under section 3.
- **needs_review** — genuinely ambiguous: a claim under section 3 that could
  go either way, or anything not clearly covered by this document. Prefer
  escalating over guessing when uncertain.
