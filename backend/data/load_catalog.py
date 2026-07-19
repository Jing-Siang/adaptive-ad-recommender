"""Ingest a product dataset (e.g. Amazon Products on Kaggle) as a proxy ad catalog,
embed each item's title + description + category with Voyage AI, and upsert into
the Pinecone `ads` namespace."""

import argparse
import csv
from pathlib import Path

from app.embeddings import embed_ads
from app.retrieval import get_index


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ingest(csv_path: Path, batch_size: int = 100) -> int:
    rows = load_rows(csv_path)
    index = get_index()
    count = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [f"{r['title']}. {r['description']}. Category: {r['category']}" for r in batch]
        vectors = embed_ads(texts)

        index.upsert(
            vectors=[
                {
                    "id": row["ad_id"],
                    "values": vector,
                    "metadata": {
                        "title": row["title"],
                        "description": row["description"],
                        "category": row["category"],
                        "price": float(row["price"]) if row.get("price") else None,
                    },
                }
                for row, vector in zip(batch, vectors)
            ],
            namespace="ads",
        )
        count += len(batch)

    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Path to catalog CSV with ad_id,title,description,category,price")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    n = ingest(args.csv_path, batch_size=args.batch_size)
    print(f"Ingested {n} ads into Pinecone namespace 'ads'.")
