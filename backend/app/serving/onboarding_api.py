from functools import lru_cache

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openai import OpenAI
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core.config import settings
from app.core.db import get_db
from app.core.embeddings import embed_query
from app.core.vector_store import fetch_vector, upsert_vector
from app.schemas import (
    CheckpointJudgment,
    OnboardingChatRequest,
    OnboardingCheckpointRequest,
    OnboardingCheckpointResponse,
)
from app.serving.retrieval import retrieve_candidates

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# How many candidates a checkpoint round shows -- a "few things you might
# like" preview, not a full feed page.
_CHECKPOINT_CANDIDATE_COUNT = 3


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_CHAT_SYSTEM_PROMPT = """You are a friendly onboarding assistant helping a new user discover what kinds of \
ads/products they might be interested in. Ask short, natural, exploratory questions -- one or two at a \
time, conversational tone, not a formal survey. The user's messages sometimes describe how they reacted to \
ads you showed them (e.g. "I liked X, wasn't interested in Y") -- treat that as real signal about their \
taste and let it inform your next question, don't just ignore it. If you are told candidates are being \
shown to the user this turn, naturally acknowledge that (e.g. "here's a few things that might interest \
you") without needing to know their specific details."""


@router.post("/chat")
def onboarding_chat(request: OnboardingChatRequest) -> StreamingResponse:
    """Streamed, user-visible conversational turn. Pure conversation over
    whatever history the client sends -- touches no DB/Pinecone state.
    Deliberately not wrapped in @retry: a mid-stream failure can't be
    usefully retried the way a single blocking call can (the client would
    need to handle a partial response either way)."""
    instructions = _CHAT_SYSTEM_PROMPT
    if request.show_candidates:
        instructions += "\n\nYou are showing the user a few candidate ads right after this reply."

    def _stream():
        stream = _get_client().responses.create(
            model=settings.openai_chat_model,
            instructions=instructions,
            input=[m.model_dump() for m in request.messages],
            stream=True,
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta

    return StreamingResponse(_stream(), media_type="text/plain")


_CHECKPOINT_PROMPT = """Given the onboarding conversation so far, decide three things:
1. show_candidates: is there concrete enough signal yet (a specific interest, not just vague words like \
"stuff" or "things") to suggest specific ads worth showing? If the user has only been vague so far, this \
should be false -- don't force it.
2. ready_to_finish: true only if show_candidates is also true AND (if candidates were already shown earlier \
in this conversation, per any messages describing reactions) those reactions were clearly positive. Never \
true if show_candidates is false in this same turn -- onboarding can't finish without ever having shown or \
tested a candidate.
3. interest_summary: a best-effort description of what this user seems interested in so far, even if still \
vague or early -- always populate this, never leave it empty."""


@retry(stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=20), reraise=True)
def _judge_checkpoint(messages: list[dict]) -> CheckpointJudgment:
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        instructions=_CHECKPOINT_PROMPT,
        input=messages,
        text_format=CheckpointJudgment,
    )
    return response.output_parsed


@router.post("/checkpoint", response_model=OnboardingCheckpointResponse)
def onboarding_checkpoint(
    request: OnboardingCheckpointRequest, db: Session = Depends(get_db)
) -> OnboardingCheckpointResponse:
    """Non-streamed structured-output side of a turn: decides whether enough
    signal exists yet to show candidates, seeds the profile the first time
    that happens, and retrieves real candidates. Call this *before*
    /onboarding/chat so the reply can be told show_candidates for this turn."""
    judgment = _judge_checkpoint([m.model_dump() for m in request.messages])

    candidates = []
    if judgment.show_candidates:
        if fetch_vector(request.user_id, namespace="users") is None:
            vector = embed_query([judgment.interest_summary])[0]
            upsert_vector(
                request.user_id,
                vector,
                metadata={"interest_summary": judgment.interest_summary},
                namespace="users",
            )
        candidates = retrieve_candidates(db, request.user_id, top_k=_CHECKPOINT_CANDIDATE_COUNT)

    return OnboardingCheckpointResponse(
        show_candidates=judgment.show_candidates,
        ready_to_finish=judgment.ready_to_finish,
        interest_summary=judgment.interest_summary,
        candidates=candidates,
    )
