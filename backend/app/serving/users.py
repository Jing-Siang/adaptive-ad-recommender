from fastapi import APIRouter, HTTPException

from app.core.embeddings import embed_query
from app.core.logging_utils import log_event
from app.core.vector_store import fetch_metadata, update_metadata, upsert_vector
from app.schemas import DoNotShowRequest, UserCreateRequest, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=201)
def create_user(request: UserCreateRequest) -> UserResponse:
    """Seed a user's starting profile vector from a free-text interest summary.
    Called once, at the first onboarding checkpoint -- retrieve_candidates
    requires this to have already run."""
    vector = embed_query([request.interest_summary])[0]
    upsert_vector(
        request.user_id,
        vector,
        metadata={"interest_summary": request.interest_summary},
        namespace="users",
    )
    log_event("user_profile_created", user_id=request.user_id)
    return UserResponse(user_id=request.user_id, interest_summary=request.interest_summary)


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: str) -> UserResponse:
    metadata = fetch_metadata(user_id, namespace="users")
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"no profile found for user '{user_id}'")
    return UserResponse(user_id=user_id, interest_summary=metadata.get("interest_summary", ""))


@router.post("/{user_id}/do-not-show", status_code=204)
def do_not_show(user_id: str, request: DoNotShowRequest) -> None:
    """Permanent per-user exclusion -- not a learning signal, so no profile
    nudge and no event log entry, just an addition to the user's blocklist
    (checked during retrieval, see retrieval.py)."""
    metadata = fetch_metadata(user_id, namespace="users")
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"no profile found for user '{user_id}'")

    blocklist = set(metadata.get("blocklist", []))
    blocklist.add(request.ad_id)
    update_metadata(user_id, metadata={"blocklist": list(blocklist)}, namespace="users")
    log_event("ad_blocklisted", user_id=user_id, ad_id=request.ad_id)
