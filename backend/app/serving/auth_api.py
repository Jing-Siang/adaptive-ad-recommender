from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    resolve_refresh_token,
    revoke_refresh_token,
    verify_google_id_token,
)
from app.core.config import settings
from app.core.db import get_db
from app.core.logging_utils import log_event
from app.models import User
from app.schemas import AccountResponse, AuthTokenResponse, CurrentUser, GoogleLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/auth",
    )


def _find_or_create_user(db: Session, claims: dict) -> User:
    """New Google accounts default to the least-privileged role --
    advertiser/moderator are assigned manually (see docs/auth_plan.md)."""
    user = db.query(User).filter_by(google_sub=claims["sub"]).first()
    if user is not None:
        return user
    user = User(
        google_sub=claims["sub"],
        email=claims["email"],
        display_name=claims.get("name", claims["email"]),
        avatar_url=claims.get("picture"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event("user_account_created", user_id=user.id, email=user.email)
    return user


@router.post("/google", response_model=AuthTokenResponse, status_code=201)
def google_login(request: GoogleLoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthTokenResponse:
    claims = verify_google_id_token(request.id_token)
    user = _find_or_create_user(db, claims)

    _set_refresh_cookie(response, create_refresh_token(user))
    log_event("user_logged_in", user_id=user.id)

    return AuthTokenResponse(access_token=create_access_token(user), user=AccountResponse.model_validate(user))


@router.post("/refresh", response_model=AuthTokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> AuthTokenResponse:
    old_token = request.cookies.get(_REFRESH_COOKIE)
    user_id = resolve_refresh_token(old_token) if old_token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="refresh token missing or expired")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="account no longer exists")

    # Rotation: the old token stops working the instant a new one is
    # issued, so a stolen-but-unused refresh token can't be replayed later.
    revoke_refresh_token(old_token)
    _set_refresh_cookie(response, create_refresh_token(user))

    return AuthTokenResponse(access_token=create_access_token(user), user=AccountResponse.model_validate(user))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(_REFRESH_COOKIE)
    if token:
        revoke_refresh_token(token)
    response.delete_cookie(_REFRESH_COOKIE, path="/auth")


@router.get("/me", response_model=AccountResponse)
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> AccountResponse:
    user = db.get(User, current.id)
    if user is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return AccountResponse.model_validate(user)
