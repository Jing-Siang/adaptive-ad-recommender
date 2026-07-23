"""Demo artifact: runs N rounds of recommend -> simulate an agentic reaction ->
update profile -> re-recommend, and prints a rolling "like rate" improving across
rounds plus one example decision trace.

The simulated reaction comes from a single-turn tool-calling LLM call: the model
is given four tools -- like, dislike, interested, no_reaction -- and picks
exactly one, based only on the persona's stated interest and the served ad's
own content. It's never told the ranking algorithm's score or rank position.
A simulator that reacted based on rank (e.g. "the top-ranked ad gets liked most
often") would be circular: it would reward whatever the algorithm already put
first, so it could never reveal a bad ranking. Judging fit independently is what
makes "does the rolling like rate improve" real evidence rather than a tautology.
"""

import argparse
from functools import lru_cache

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.embeddings import embed_query
from app.core.vector_store import fetch_vector, upsert_vector
from app.models import Event
from app.schemas import AdCandidate
from app.serving.feedback import record_feedback
from app.serving.ranking import rerank
from app.serving.retrieval import retrieve_candidates


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_JUDGE_PROMPT = """You are simulating one specific real user's authentic reaction to an ad, based \
only on their stated interests -- never on how confident or well-ranked the ad system claims the \
ad is. Call exactly one of the four tools, based on which one genuinely matches how this user \
would feel:
- like: mild genuine interest
- interested: strong genuine interest, would act on it
- dislike: actively unwanted/annoying/objectionable -- a real negative reaction, not just irrelevance
- no_reaction: doesn't relate to their interests, but isn't annoying either -- just a miss, not a strike
In real behavior, most ads a user sees don't provoke like/dislike/interested at all -- reserve \
those three for a genuine, clear reaction, and default to no_reaction when in doubt."""

_JUDGE_TOOLS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    }
    for name, description in [
        ("like", "The ad is a genuine positive fit for the stated interests."),
        ("dislike", "The ad is actively annoying, unwanted, or objectionable given the stated interests."),
        ("interested", "The ad is a strong, compelling fit worth acting on."),
        ("no_reaction", "The ad is simply irrelevant or unrelated to the stated interests -- not annoying, just not applicable."),
    ]
]


@retry(stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=20), reraise=True)
def _judge_reaction(persona_interest: str, served_ad: AdCandidate) -> str:
    response = _get_client().responses.create(
        model=settings.openai_chat_model,
        instructions=_JUDGE_PROMPT,
        input=(
            f"This user's stated interests: {persona_interest!r}\n\n"
            f"Ad they were just shown:\n"
            f"headline: {served_ad.headline!r}\n"
            f"description: {served_ad.description!r}\n"
            f"category: {served_ad.category}"
        ),
        tools=_JUDGE_TOOLS,
    )
    for item in response.output:
        if item.type == "function_call":
            return item.name
    return "no_reaction"


def simulate_reaction(persona_interest: str, served_ad: AdCandidate | None) -> str | None:
    """Single-turn agentic reaction: the model picks among like/dislike/interested/
    no_reaction based only on the persona's stated interest and the served ad's
    actual content -- never the ranking algorithm's score or rank, which would
    make this circular (rewarding whatever the algorithm already put first
    instead of judging genuine fit). no_reaction is an explicit tool rather
    than "call nothing" -- live testing showed the model over-eagerly called
    dislike for merely-irrelevant ads when abstaining meant calling no tool at
    all; giving it an explicit opt-out fixed that."""
    if served_ad is None:
        return None
    reaction = _judge_reaction(persona_interest, served_ad)
    return reaction if reaction != "no_reaction" else None


def _ensure_profile(user_id: str, interest_text: str) -> None:
    """retrieve_candidates now requires a profile to already exist (normally
    created by onboarding) -- seed one here so this CLI script keeps working
    standalone."""
    if fetch_vector(user_id, namespace="users") is None:
        vector = embed_query([interest_text])[0]
        upsert_vector(user_id, vector, metadata={"interest_summary": interest_text}, namespace="users")


def run(user_id: str, rounds: int, top_k: int, interest_text: str) -> None:
    like_history = []
    last_trace = None
    _ensure_profile(user_id, interest_text)
    db = SessionLocal()

    try:
        for round_num in range(1, rounds + 1):
            candidates = retrieve_candidates(db, user_id, top_k=top_k)
            rankings = rerank(user_context=user_id, candidates=candidates)
            ranked = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)
            candidates_by_id = {c.ad_id: c for c in candidates}

            served_id = ranked[0].ad_id if ranked else None
            served_ad = candidates_by_id.get(served_id) if served_id else None
            last_trace = {"round": round_num, "candidates": candidates, "rankings": ranked, "served": served_id}

            reaction = None
            if served_id:
                # log the impression regardless of reaction, same as the real feed does
                db.add(Event(user_id=user_id, campaign_id=int(served_id), event_type="impression"))
                db.commit()

                reaction = simulate_reaction(interest_text, served_ad)
                if reaction:
                    record_feedback(db, user_id, served_id, reaction)
                    db.add(Event(user_id=user_id, campaign_id=int(served_id), event_type=reaction))
                    db.commit()

            like_history.append(1 if reaction == "like" else 0)
            category = served_ad.category if served_ad else None
            print(f"round {round_num}: served={served_id} category={category} reaction={reaction}")
    finally:
        db.close()

    window = 5
    rolling_like_rate = [
        sum(like_history[max(0, i - window) : i + 1]) / len(like_history[max(0, i - window) : i + 1])
        for i in range(len(like_history))
    ]
    print("\nRolling like rate by round:", [round(c, 2) for c in rolling_like_rate])
    print("(fraction of served ads the simulated persona liked, decided independently of")
    print(" the ranker's own score -- the actual 'is it learning' signal, not a tautology)")
    print("\nExample decision trace (final round):")
    print(last_trace)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--interest",
        default="need a plumber for a leaky faucet",
        help="Cold-start interest text -- also the persona description the reaction judge uses as ground truth",
    )
    args = parser.parse_args()

    run(args.user_id, args.rounds, args.top_k, args.interest)
