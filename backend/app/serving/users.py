from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.logging_utils import log_event
from app.core.vector_store import delete_vector, fetch_metadata, update_metadata
from app.models import Reaction, User
from app.schemas import CurrentUser, DoNotShowRequest

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/me/do-not-show", status_code=204)
def do_not_show(request: DoNotShowRequest, current: CurrentUser = Depends(get_current_user)) -> None:
    """Permanent per-user exclusion -- not a learning signal, so no profile
    nudge and no event log entry, just an addition to the user's blocklist
    (checked during retrieval, see retrieval.py)."""
    user_id = str(current.id)
    metadata = fetch_metadata(user_id, namespace="users")
    if metadata is None:
        raise HTTPException(status_code=404, detail="no profile found")

    blocklist = set(metadata.get("blocklist", []))
    blocklist.add(request.ad_id)
    update_metadata(user_id, metadata={"blocklist": list(blocklist)}, namespace="users")
    log_event("ad_blocklisted", user_id=user_id, ad_id=request.ad_id)


@router.post("/me/reset", status_code=204)
def reset_my_profile(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    """"Restart onboarding," now that user_id is a real account instead of
    a throwaway per-session UUID (see docs/auth_plan.md): wipes the
    caller's profile vector + blocklist so onboarding_checkpoint treats
    them as brand new, and their Reaction rows so a later re-reaction to
    an already-reacted ad is a true first reaction again, not a partial
    delta against a profile that no longer has any memory of the original
    nudge. Deliberately does NOT touch budget_spent or Event -- the money
    was legitimately spent by real past clicks, and Event is a genuine
    append-only history, neither of which a profile reset should undo.
    No-op (still 204) if there was no profile to begin with. Also flips
    onboarding_completed back to False -- restarting onboarding should
    actually route back into the onboarding flow, not the feed (see
    OnboardingFeedPage's use of that flag, not profile-vector existence,
    to decide which to show)."""
    user_id = str(current.id)
    delete_vector(user_id, namespace="users")
    db.execute(delete(Reaction).where(Reaction.user_id == current.id))
    db.query(User).filter(User.id == current.id).update({"onboarding_completed": False})
    db.commit()
    log_event("user_profile_reset", user_id=user_id)
