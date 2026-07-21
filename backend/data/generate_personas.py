"""Generate synthetic user personas with an LLM-written browsing/interest history,
clearly labeled as synthetic. Embeds each persona's rolling profile and upserts it
into the Pinecone `users` namespace."""

import argparse
from functools import lru_cache

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.core.embeddings import embed_query
from app.serving.retrieval import get_index


class Persona(BaseModel):
    user_id: str
    interest_summary: str


class PersonaBatch(BaseModel):
    personas: list[Persona]


@lru_cache
def _get_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


_PROMPT = """Generate {n} distinct synthetic user personas for an ad-recommendation demo.
Each persona should have a short, plausible browsing/interest history (2-3 sentences)."""


def generate_personas(n: int = 20) -> list[Persona]:
    response = _get_client().responses.parse(
        model=settings.openai_chat_model,
        input=_PROMPT.format(n=n),
        text_format=PersonaBatch,
    )
    return response.output_parsed.personas


def index_personas(personas: list[Persona]) -> None:
    index = get_index()
    vectors = embed_query([p.interest_summary for p in personas])
    index.upsert(
        vectors=[
            {
                "id": p.user_id,
                "values": vector,
                "metadata": {"interest_summary": p.interest_summary, "synthetic": True},
            }
            for p, vector in zip(personas, vectors)
        ],
        namespace="users",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20, help="Number of personas to generate")
    args = parser.parse_args()

    personas = generate_personas(args.n)
    index_personas(personas)
    print(f"Generated and indexed {len(personas)} synthetic personas.")
