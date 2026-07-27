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
taste and let it inform your next question, don't just ignore it."""

# Deterministic last resort if the model still asks a question after every
# retry below -- guarantees the final turn is never a follow-up question,
# even if every attempt fails.
_FINISH_FALLBACK_REPLY = "Great chatting with you! Your personalized feed is ready -- go check it out!"


def _generate_finish_reply(instructions: str, input_messages: list[dict]) -> str:
    """Non-streamed, validated generation for the final onboarding turn.
    Tested live: even with a front-loaded, explicit "do not ask a question"
    instruction, gpt-4o-mini still asked one anyway roughly half the time --
    a real reliability gap, not a prompt-wording problem, so prompting alone
    isn't enough here. Retries (plain, no backoff needed -- this isn't a rate
    limit/error case, just resampling) until a reply with no "?" comes back,
    falling back to a fixed closing line if it never does."""
    for _ in range(3):
        response = _get_client().responses.create(
            model=settings.openai_chat_model, instructions=instructions, input=input_messages
        )
        if "?" not in response.output_text:
            return response.output_text
    return _FINISH_FALLBACK_REPLY


@router.post("/chat")
def onboarding_chat(request: OnboardingChatRequest) -> StreamingResponse:
    """Streamed, user-visible conversational turn. Pure conversation over
    whatever history the client sends -- touches no DB/Pinecone state.
    Deliberately not wrapped in @retry: a mid-stream failure can't be
    usefully retried the way a single blocking call can (the client would
    need to handle a partial response either way)."""
    # No branch for "candidates are about to be shown" -- getting the model
    # to reliably narrate specific candidate content in its freeform reply
    # turned out to be a dead end (tested live: a front-loaded "must name
    # each of N" instruction worked in a short/fresh conversation but
    # completely failed once there was a longer, topically-focused
    # conversation history for the model's attention to latch onto instead).
    # The reply doesn't need to change based on whether candidates are shown
    # -- see OnboardingChatRequest's docstring for where that signal lives
    # instead. ready_to_finish is the only turn that needs different
    # instructions, since it needs to stop asking questions and close out.
    instructions = _CHAT_SYSTEM_PROMPT
    if request.ready_to_finish:
        instructions = (
            "IMPORTANT -- this is the final onboarding turn. The user is done answering questions; "
            "onboarding is complete. Your reply this turn must NOT ask any question of any kind (no "
            '"what about...", no "do you prefer...", nothing). Instead, write a short (1-2 sentence), warm '
            "closing message that acknowledges their taste and clearly tells them their personalized feed "
            "is ready, inviting them to go check it out now. This completely overrides the \"ask "
            "exploratory questions\" behavior described below -- that only applies to earlier turns, not "
            "this one.\n\n"
        ) + instructions

    input_messages = [m.model_dump() for m in request.messages]

    def _stream():
        if request.ready_to_finish:
            # Non-streamed + validated instead of the real token stream below --
            # see _generate_finish_reply for why this one turn needs that.
            yield _generate_finish_reply(instructions, input_messages)
            return
        stream = _get_client().responses.create(
            model=settings.openai_chat_model,
            instructions=instructions,
            input=input_messages,
            stream=True,
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta

    return StreamingResponse(_stream(), media_type="text/plain")


_CHECKPOINT_PROMPT = """Given the onboarding conversation so far, decide three things:
1. show_candidates: is there concrete enough signal yet (a specific interest, not just vague words like \
"stuff" or "things") to suggest specific ads worth showing right now? If the user has only been vague so \
far, this should be false -- don't force it. Also false if onboarding is ready to finish (see \
ready_to_finish) -- no need for a fresh candidate preview right before handing the user off to their full feed.
2. ready_to_finish: true once candidates were shown in an earlier turn (per messages describing reactions) \
and those reactions were clearly positive. This can be true even when show_candidates is false this turn --
finishing doesn't require showing a fresh batch of candidates on the same turn, only having already shown \
and tested at least one earlier.
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
    # Belt-and-suspenders alongside the prompt's own guidance: never surface a
    # fresh candidate batch on the same turn onboarding is wrapping up, even
    # if the judge call still returns both flags true.
    if judgment.show_candidates and not judgment.ready_to_finish:
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
