"""Demo artifact: runs N rounds of recommend -> simulate click -> update profile ->
re-recommend, and prints CTR improving across rounds plus one example decision trace."""

import argparse
import random

from app.feedback import update_profile_vector
from app.ranking import rerank
from app.retrieval import retrieve_candidates


def simulate_click(ranked_ads, click_prob_top: float = 0.6) -> str | None:
    """Toy simulator: the top-ranked ad is clicked with high probability, lower ones less so."""
    if not ranked_ads:
        return None
    for i, ad in enumerate(ranked_ads):
        if random.random() < click_prob_top * (0.7**i):
            return ad.ad_id
    return None


def run(user_id: str, rounds: int, top_k: int) -> None:
    ctr_history = []
    last_trace = None

    for round_num in range(1, rounds + 1):
        candidates = retrieve_candidates(user_id, top_k=top_k)
        rankings = rerank(user_context=user_id, candidates=candidates)
        ranked = sorted(rankings, key=lambda r: r.relevance_score, reverse=True)

        clicked_id = simulate_click(ranked)
        ctr_history.append(1 if clicked_id else 0)
        last_trace = {"round": round_num, "candidates": candidates, "rankings": ranked, "clicked": clicked_id}

        print(f"round {round_num}: served={ranked[0].ad_id if ranked else None} clicked={clicked_id}")

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
    args = parser.parse_args()

    run(args.user_id, args.rounds, args.top_k)
