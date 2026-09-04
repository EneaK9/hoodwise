"""Embed chunks with OpenAI text-embedding-3-small (1536-dim)."""

from __future__ import annotations

from app.config import settings
from app.db import get_conn


def _client():
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = _client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")
    vectors: list[list[float]] = []
    batch = 64
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(
            model=settings.embedding_model,
            input=texts[i : i + batch],
        )
        vectors.extend([row.embedding for row in resp.data])
    return vectors


def embed_document(document_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content FROM chunks WHERE document_id = %s AND embedding IS NULL",
                (document_id,),
            )
            rows = list(cur.fetchall())
        if not rows:
            return 0
        vectors = embed_texts([r["content"] for r in rows])
        with conn.cursor() as cur:
            for row, vec in zip(rows, vectors, strict=True):
                cur.execute(
                    """
                    UPDATE chunks
                       SET embedding = %s, embedding_model = %s
                     WHERE id = %s
                    """,
                    (vec, settings.embedding_model, row["id"]),
                )
            cur.execute(
                "UPDATE documents SET status = 'embedded' WHERE id = %s",
                (document_id,),
            )
        conn.commit()
    return len(rows)
