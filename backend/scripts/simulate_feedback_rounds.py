"""Demo artifact: runs N rounds of recommend -> simulate a like reaction -> update
profile -> re-recommend, and prints CTR improving across rounds plus one example
decision trace."""

import argparse
import random

from app.core.db import SessionLocal
from app.core.embeddings import embed_query
from app.core.vector_store import fetch_vector, upsert_vector
from app.serving.feedback import record_feedback
from app.serving.ranking import rerank
from app.serving.retrieval import retrieve_candidates


def _ensure_profile(user_id: str, interest_text: str) -> None:
    """retrieve_candidates now requires a profile to already exist (normally
    created by onboarding) -- seed one here so this CLI script keeps working
    standalone."""
    if fetch_vector(user_id, namespace="users") is None:
        vector = embed_query([interest_text])[0]
        upsert_vector(user_id, vector, metadata={"interest_summary": interest_text}, namespace="users")


def simulate_reaction(ranked_ads, like_prob_top: float = 0.6) -> str | None:
    """Toy simulator: the top-ranked ad gets a "like" with high probability, lower
    ones less so. No reaction at all (the common case) is possible and does not
    nudge the profile -- there's no implicit negative signal anymore, only an
    explicit dislike, which this simulator doesn't model."""
    if not ranked_ads:
        return None
    for i, ad in enumerate(ranked_ads):
        if random.random() < like_prob_top * (0.7**i):
            return ad.ad_id
    return None


def run(user_id: str, rounds: int, top_k: int, interest_text: str) -> None:
    ctr_history = []
    last_trace = None
    _ensure_profile(user_id, interest_text)
    db = SessionLocal()

    try:
        for round_num in range(1, rounds + 1):
            candidates = retrieve_candidates(db, user_id, top_k=top_k)
            rankings = rerank(user_context=user_id, candidates=candidates)
            ranked = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)

            liked_id = simulate_reaction(ranked)
            ctr_history.append(1 if liked_id else 0)
            last_trace = {"round": round_num, "candidates": candidates, "rankings": ranked, "liked": liked_id}

            served_id = ranked[0].ad_id if ranked else None
            if served_id and liked_id == served_id:
                record_feedback(db, user_id, served_id, "like")

            print(f"round {round_num}: served={served_id} liked={liked_id}")
    finally:
        db.close()

    window = 5
    rolling_ctr = [
        sum(ctr_history[max(0, i - window) : i + 1]) / len(ctr_history[max(0, i - window) : i + 1])
        for i in range(len(ctr_history))
    ]
    print("\nRolling CTR by round:", [round(c, 2) for c in rolling_ctr])
    print("\nExample decision trace (final round):")
    print(last_trace)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--interest",
        default="general shopper",
        help="Cold-start interest text used to seed a profile if none exists yet",
    )
    args = parser.parse_args()

    run(args.user_id, args.rounds, args.top_k, args.interest)
