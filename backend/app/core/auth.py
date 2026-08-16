"""JWT issuing/verification + refresh-token storage. See docs/auth_plan.md
for the full design and why: our own short-lived, stateless JWT access
token (no DB/Redis hit to verify -- read the claims straight off it) paired
with an opaque refresh token tracked in Redis (the one piece of real
server-side state, needed because a signed JWT can't be un-issued -- Redis
is what makes logout/rotation actually revoke something)."""

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings
from app.core.queue import redis_conn
from app.models import User
from app.schemas import CurrentUser

_JWT_ALGORITHM = "HS256"
_REFRESH_KEY_PREFIX = "refresh_token:"

_bearer_scheme = HTTPBearer(auto_error=False)


def verify_google_id_token(id_token: str) -> dict:
    """Verifies the token against Google's public keys and checks it was
    actually issued for our app (the `aud` claim) -- raises if either
    check fails, so callers can trust every field in the returned dict."""
    try:
        return google_id_token.verify_oauth2_token(id_token, google_requests.Request(), settings.google_client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid Google token") from exc


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALGORITHM)


def create_refresh_token(user: User) -> str:
    """Opaque random token (not a JWT -- nothing to decode, its only job is
    to be an unguessable key into Redis). TTL matches its own expiry, so
    Redis expires it on its own; no cleanup job needed."""
    token = secrets.token_urlsafe(32)
    ttl_seconds = settings.refresh_token_expire_days * 24 * 60 * 60
    redis_conn.set(f"{_REFRESH_KEY_PREFIX}{token}", str(user.id), ex=ttl_seconds)
    return token


def resolve_refresh_token(token: str) -> int | None:
    """The user id it belongs to, or None if it's missing/expired/never
    existed -- callers can't tell those apart, which is the point (an
    already-consumed or forged token looks identical to a stale one)."""
    raw = redis_conn.get(f"{_REFRESH_KEY_PREFIX}{token}")
    return int(raw) if raw is not None else None


def revoke_refresh_token(token: str) -> None:
    """Used on logout, and on every refresh (rotation) -- deleting the old
    token before/alongside issuing a new one means a stolen, already-used
    refresh token stops working instead of staying valid indefinitely."""
    redis_conn.delete(f"{_REFRESH_KEY_PREFIX}{token}")


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    return CurrentUser(id=int(payload["sub"]), email=payload["email"], role=payload["role"])


def require_role(*roles: str):
    """Dependency factory, not a dependency itself -- `Depends(require_role("moderator"))`
    wraps get_current_user and additionally checks the role."""

    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"requires role: {', '.join(roles)}")
        return user

    return _check
