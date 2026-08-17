# Authentication (Google OAuth) + role-based authorization plan

This app currently has zero authentication: every endpoint takes a
caller-supplied `user_id` string (or, for campaigns, no identity concept at
all) with no verification. Two concrete problems this causes today:

1. **`POST /campaigns/{id}/moderate` has no access control whatsoever** --
   anyone who can reach the API can approve or reject any campaign. This is
   the most urgent gap.
2. **Every `user_id` field is spoofable** -- any caller can claim to be any
   user and react/onboard/fetch recommendations as them, since nothing
   verifies identity.

Roles are a separate concept from login: Google tells us *who* someone is,
not *what they're allowed to do here*. New Google accounts default to the
least-privileged role; `advertiser`/`moderator` are assigned manually
(direct DB update) -- this project's actual user base is small enough that
a self-service request/approval flow would be over-engineering.

## Phase 0 -- design decisions (locked in)

- [x] Login via **Google OAuth only**, no email/password.
- [x] Issue our own **JWT access token + refresh token pair** after Google
      verifies identity, not a raw Google token, so we control our own
      session lifetime/claims/roles.
- [x] **Access token in localStorage**, **refresh token in an httpOnly
      cookie** -- the access token is short-lived and low-risk if stolen;
      the refresh token is long-lived and more sensitive, so it stays out
      of reach of JavaScript (XSS mitigation).
- [x] Refresh tokens tracked server-side in **Redis** (already running,
      for RQ) so they can be revoked on logout and rotated on use -- a
      plain JWT can't be un-issued once signed, which is why the refresh
      token needs real server-side state while the access token stays
      fully stateless (no DB/Redis hit to verify it).
- [x] Role model: single `role` column, one of `end_user` (default) /
      `advertiser` / `moderator`, same "Pydantic-layer enum, not DB enum"
      convention already used for `Campaign.status`/`Event.event_type`.
      `moderator` is a superset of `advertiser`/`end_user`, not a separate
      multi-role table.
- [x] OAuth flow shape: frontend-driven via Google Identity Services
      (`@react-oauth/google`) -- the frontend gets a Google-signed ID
      token directly in the browser and POSTs it to our backend once, no
      server-side redirect/callback/state-nonce handling to build.

## Role model

- **`end_user`** (default on first login) -- onboarding, feed, reactions,
  own profile. Matches the existing "consumer" persona (View 1).
- **`advertiser`** -- everything above, plus submit campaigns (View 3) and
  view the performance dashboard (View 2). `Campaign.user_id` points
  straight at the submitting account (Phase 5 below) -- no separate
  Advertiser entity. Still no per-advertiser *visibility* scoping,
  though: `GET /campaigns` shows every advertiser's campaigns to every
  advertiser, not just their own; that's a real follow-on, out of scope
  here to keep this bounded.
- **`moderator`** -- everything above, plus `POST /campaigns/{id}/moderate`
  (View 4).

## Phase 1 -- backend foundations

- [x] `requirements.txt`: add `PyJWT`, `google-auth`.
- [x] New `User` model (`app/models.py`) + Alembic migration:
      `id`, `google_sub` (unique), `email` (unique), `display_name`,
      `avatar_url`, `role` (default `end_user`), `created_at`.
      `Reaction.user_id` becomes a real `ForeignKey("users.id")` -- the
      reactions-table design already anticipated this exact moment ("only
      user_id's type/constraint would ever need to change"). `Event.user_id`
      stays a loose string -- pure log data, no need to touch it.
- [x] `app/core/config.py`: add `google_client_id`, `jwt_secret`,
      `access_token_expire_minutes` (15), `refresh_token_expire_days` (30).
- [x] New `app/core/auth.py`:
      - `create_access_token(user)` -- JWT, HS256, 15 min, claims
        `sub`/`role`/`email`.
      - `create_refresh_token(user)` -- opaque `secrets.token_urlsafe(32)`,
        stored in Redis via the existing `redis_conn`
        (`app/core/queue.py`) as `refresh_token:{token}` -> user id, `EX`
        set to the refresh lifetime (free auto-expiry, no cleanup job).
      - `get_current_user(...)` -- FastAPI dependency, decodes/verifies
        the JWT from `Authorization: Bearer <token>`. Reads claims
        directly, no DB hit per request. 401 on missing/invalid/expired.
      - `require_role(*roles)` -- dependency factory wrapping
        `get_current_user`, 403 if `user.role` isn't in the allowed set.
- [x] New `app/serving/auth_api.py` (`/auth` prefix):
      - `POST /auth/google` -- body `{id_token}`. Verifies with Google
        (`google.oauth2.id_token.verify_oauth2_token`, checks `aud`
        matches our client ID), find-or-creates the `User` row by
        `google_sub`, issues both tokens (access token in the JSON body,
        refresh token as an httpOnly/Secure/SameSite=Lax cookie).
      - `POST /auth/refresh` -- reads the refresh token from the cookie,
        validates against Redis, rotates it (delete old key, issue+store
        new one), returns a new access token.
      - `POST /auth/logout` -- deletes the Redis entry, clears the cookie.
      - `GET /auth/me` -- returns the current user (via `get_current_user`)
        for the frontend to restore session state on page load.
- [x] Register the new router in `app/main.py`.

## Phase 2 -- role-gate the urgent endpoints

- [x] `campaigns/api.py`: `create_campaign` ->
      `Depends(require_role("advertiser", "moderator"))`,
      `moderate_campaign` -> `Depends(require_role("moderator"))`.
- [x] `performance_api.py`: `get_performance` ->
      `Depends(require_role("advertiser", "moderator"))`.

This closes the actual security gap (unauthenticated moderation) first,
independent of the rest of the migration below.

## Phase 3 -- frontend auth plumbing

- [x] `npm install @react-oauth/google`.
- [x] New `src/contexts/AuthContext.tsx`: holds `user`/`accessToken`/
      `loading`. `login(idToken)` POSTs to `/auth/google`. `logout()`
      POSTs to `/auth/logout` then clears state. On mount, if a token
      exists in localStorage, calls `/auth/me` to validate it, falling
      back to `/auth/refresh` (cookie sent automatically) before giving
      up and showing the login screen.
- [x] `src/api.ts`: `request()` attaches `Authorization: Bearer <token>`
      automatically, sets `credentials: 'include'`. On a 401, attempts
      one silent `/auth/refresh` + retry before surfacing the error. Adds
      `googleLogin`, `refreshAccessToken`, `logout`, `fetchMe`.
- [x] Login gate: wrap `<App>` (`src/App.tsx`) so Google's Sign-In button
      renders when `user` is null, the real app otherwise.
- [x] Role-gated routes: `/campaigns` requires `advertiser`/`moderator`,
      `/moderator` requires `moderator` -- redirect or show a plain "not
      authorized" message otherwise.

## Phase 4 -- migrate remaining endpoints + fix restart-onboarding

- [x] `events_api.py`, `onboarding_api.py`, `serving/api.py`,
      `serving/users.py`: add `Depends(get_current_user)`, remove
      `user_id` from the request schemas (`ImpressionRequest`,
      `ReactionRequest`, `ReactionClearRequest`, `ReportRequest`,
      `RecommendationRequest`, `BatchRecommendationRequest`,
      `OnboardingChatRequest`, `OnboardingCheckpointRequest`,
      `UserCreateRequest`), use the authenticated user's id instead of
      the client-supplied field.
- [x] `src/api.ts`: remove the `userId` parameter from every function
      that currently takes one explicitly (`sendReaction`,
      `clearReaction`, `logImpression`, etc.). Added `resetProfile()`
      (`POST /users/me/reset`) and pointed `createUser`/`getUser` at
      `/users/me` instead of a path-param user id.
- [x] `OnboardingFeedPage.tsx`: drop the `crypto.randomUUID()`-per-session
      pattern -- `userId` now comes from `useAuth().user.id` implicitly
      (the backend derives it from the token, so the page no longer
      needs to hold or pass it at all). `OnboardingChat`/`Feed`/
      `FeedCard` had their `userId` prop dropped entirely (pure pass-
      through, no longer needed).
- [x] **Onboarding-vs-feed signal, revised**: initially decided via
      `GET /users/me` (profile-vector existence), then corrected -- a
      profile vector gets seeded much earlier than "done," on the first
      `onboarding_checkpoint` round that returns `show_candidates=True`
      (often after just one message). Using vector-existence would send a
      mid-conversation reload straight to the feed. Added a real
      `User.onboarding_completed` column (migration
      `c835b6e52c74`) plus `POST /onboarding/complete`, called only when
      the user actually clicks "Continue to your feed" -- that flag,
      returned on `Account`/`GET /auth/me`, is now the actual signal.
      `POST /users/me/reset` also flips it back to `False`. `AuthContext`
      gained `updateUser()` so the frontend can sync this flag locally
      right after finishing/resetting, without a full page reload.
- [x] **"Restart onboarding," kept and fixed, not removed.** Once
      `user_id` is a real account, it can no longer mean "become a new
      anonymous guest." New `POST /users/me/reset` (`app/serving/users.py`,
      derives the user from the token, no `user_id` param -- only your
      own profile is resettable):
      - deletes the caller's Pinecone profile vector + blocklist
        metadata, returning them to the "no profile yet" state
        `onboarding_checkpoint` already handles.
      - deletes the caller's `Reaction` rows directly (`DELETE FROM
        reactions WHERE user_id = ...`) -- **not** routed through
        `clear_feedback`'s refund logic. Necessary, not optional:
        `record_feedback`'s delta math (`LEARNING_RATE[new] -
        LEARNING_RATE[old_or_none]`) assumes the `Reaction` row's state
        and the profile vector's history stay in sync. If a `Reaction`
        row survived the reset, re-reacting to that same ad later would
        compute a *partial* delta against a profile vector that has no
        memory of the original nudge (it was just wiped) -- under-
        applying the nudge, and on the budget side incorrectly refunding
        money for a click that genuinely already happened, rather than
        charging fresh. Deleting the rows makes any post-reset reaction
        a true first reaction again, matching what the fresh profile
        vector actually is.
      - deliberately does **not** touch `budget_spent` while doing this
        -- the money was legitimately spent by a real past click;
        resetting your own recommendation profile shouldn't reach back
        and un-spend an advertiser's budget.
      - `Event` stays untouched either way -- not read anywhere in the
        delta computation, purely an append-only log.
      Frontend's reset button calls `resetProfile()` instead of
      generating a new UUID, then forces `OnboardingChat` to remount
      (a `key` bump) so its local chat state clears too.

## Phase 5 -- drop Advertiser, Campaign.user_id -> User directly

- [x] Removed the `Advertiser` table entirely (migration `bed5a742999d`)
      instead of linking it to `User` -- it predated the whole auth build
      as the *only* identity concept in the system (find-or-create by a
      free-text `advertiser_name` string, no verification at all: any
      caller could submit a campaign under any other advertiser's name).
      Once real accounts existed, keeping it as a second, separate
      identity layer alongside `User` was redundant, not a feature --
      there's no legitimate case in this app for an advertiser's business
      name to differ from their Google account's `display_name`.
      `Campaign.user_id` (`FK -> users.id`) now points straight at the
      submitting account. Pre-existing dev-only `Campaign` rows (mostly
      the 288-campaign seed catalog + pytest artifacts, no real owner to
      recover) were backfilled to whichever `User` row happened to exist
      at migration time, same call as the `onboarding_completed` and
      `reactions.user_id` migrations before it.
      `CampaignCreateRequest` dropped `advertiser_name` entirely; the
      frontend's campaign form no longer asks for one.
- [ ] **Still not done, deliberately out of scope for this phase**:
      per-advertiser *visibility* scoping. `GET /campaigns` still returns
      every campaign regardless of who's asking -- an `advertiser`
      account sees other advertisers' campaigns too, not just their own.

## Verification

- `make test` after each backend phase.
- Live check: register a test Google OAuth client, log in through the
  real frontend, confirm `/auth/me` returns the right user/role, confirm
  an `end_user` gets 403 from `/campaigns` and `/campaigns/{id}/moderate`,
  confirm a manually-promoted `moderator` account succeeds.
- Confirm refresh actually works: wait out the 15-minute access token
  expiry (or temporarily shorten it for testing), confirm a request
  transparently refreshes instead of forcing re-login.
- Confirm logout: refresh token's Redis key is gone, a subsequent
  `/auth/refresh` with the old cookie returns 401.
- Confirm the reset endpoint: react to an ad, reset, react to the same ad
  again with a different outcome, confirm it's treated as a fresh first
  reaction (full nudge/debit), not a partial delta against the old value.
