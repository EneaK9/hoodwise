"""Hybrid retrieval: spec rows by SQL plus chunk search by pgvector and tsvector.

The search phrase comes from the understanding model, already cleaned. Retrieval does
no interpretation of its own: no stop lists, no topic tables, no special cases per part.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.db import fetch_all
from ingestion.embed import embed_texts


def _terms(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9./-]+", phrase or "") if len(w) > 1][:12]


def _ts_or_query(phrase: str) -> str:
    keys = [re.sub(r"[^a-z0-9]+", "", k.lower()) for k in _terms(phrase)]
    keys = [k for k in keys if k]
    return " | ".join(keys) if keys else "specification"


def lookup_specs(phrase: str, variant_id: str | None, limit: int = 6, vehicle_id: str | None = None) -> list[dict[str, Any]]:
    keys = _terms(phrase)
    if not keys:
        return []
    like_terms = [f"%{k}%" for k in keys]
    rows = fetch_all(
        """
        SELECT s.id, s.part_name, s.spec_type, s.value_raw, s.value_nm, s.unit,
               s.condition_note, s.replace_required, s.torque_sequence, s.page_number,
               s.crop_path, s.verification_status, s.raw_context,
               d.doc_id, d.section_name
          FROM specs s
          JOIN documents d ON d.id = s.document_id
         WHERE (
                 to_tsvector('english', s.part_name || ' ' || coalesce(s.raw_context,'') || ' ' || coalesce(s.condition_note,'') || ' ' || s.value_raw)
                 @@ websearch_to_tsquery('english', %s)
               OR s.part_name ILIKE ANY(%s)
               OR s.raw_context ILIKE ANY(%s)
              )
           AND (s.variant_id IS NULL OR %s::uuid IS NULL OR s.variant_id = %s::uuid)
           AND (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
         ORDER BY s.verification_status = 'flagged', s.page_number
         LIMIT %s
        """,
        (phrase, like_terms, like_terms, variant_id, variant_id, vehicle_id, vehicle_id, limit * 4),
    )
    scored: list[tuple[int, dict]] = []
    lowered = [k.lower() for k in keys]
    for row in rows:
        blob = " ".join(str(row.get(k) or "") for k in ("part_name", "raw_context", "condition_note", "value_raw")).lower()
        score = sum(1 for k in lowered if k in blob)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _keyword_chunks(phrase: str, limit: int, vehicle_id: str | None) -> list[dict[str, Any]]:
    ts_q = _ts_or_query(phrase)
    likes = [f"%{k}%" for k in _terms(phrase)] or ["%specification%"]
    return fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content,
               d.doc_id, d.section_name,
               ts_rank_cd(c.tsv, to_tsquery('english', %s)) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE (c.tsv @@ to_tsquery('english', %s) OR c.content ILIKE ANY(%s))
           AND (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
         ORDER BY rank DESC NULLS LAST
         LIMIT %s
        """,
        (ts_q, ts_q, likes, vehicle_id, vehicle_id, limit),
    )


def _vector_chunks(phrase: str, limit: int, vehicle_id: str | None) -> list[dict[str, Any]]:
    if not settings.openai_api_key:
        return []
    try:
        vec = embed_texts([phrase])[0]
    except Exception:
        return []
    return fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content,
               d.doc_id, d.section_name,
               1 - (c.embedding <=> %s::vector) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE c.embedding IS NOT NULL
           AND (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
         ORDER BY c.embedding <=> %s::vector
         LIMIT %s
        """,
        (vec, vehicle_id, vehicle_id, vec, limit),
    )


def _rrf_pages(keyword: list[dict], vector: list[dict], k: int = 60, limit: int = 6) -> list[tuple[str, int]]:
    """Reciprocal rank fusion at page level: every chunk of a page adds to that page's
    score, so a table page with several matching fragments beats a page with one."""
    scores: dict[tuple[str, int], float] = {}
    for ranked in (keyword, vector):
        for rank, row in enumerate(ranked, start=1):
            if row.get("page_number") is None:
                continue
            key = (str(row.get("doc_id")), int(row["page_number"]))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: scores[key], reverse=True)[:limit]


def _with_neighbours(pages: list[tuple[str, int]], top: int = 2) -> list[tuple[str, int]]:
    """Specification tables run across page breaks; bring the page before and after the
    best hits. Structural, not vocabulary."""
    out = list(pages)
    for doc, page in pages[:top]:
        for neighbour in (page - 1, page + 1):
            if neighbour >= 1 and (doc, neighbour) not in out:
                out.append((doc, neighbour))
    return out


def _whole_pages(vehicle_id: str | None, keys: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Give the model whole pages, not fragments: a table row and its header often live in
    different chunks. Keeps the given order, merges every chunk of each page."""
    if not keys:
        return []
    pages = [k[1] for k in keys]
    docs = sorted({k[0] for k in keys if k[0]})
    rows = fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content, d.doc_id, d.section_name
          FROM chunks c JOIN documents d ON d.id = c.document_id
         WHERE (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
           AND c.page_number = ANY(%s) AND d.doc_id = ANY(%s)
         ORDER BY c.page_number, c.id
        """,
        (vehicle_id, vehicle_id, pages, docs),
    )
    merged: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        key = (str(r["doc_id"]), int(r["page_number"]))
        if key not in merged:
            merged[key] = dict(r)
        elif r["content"] and r["content"] not in merged[key]["content"]:
            merged[key]["content"] = (merged[key]["content"] + "\n" + r["content"])[:20000]
    return [merged[k] for k in keys if k in merged]


def retrieve(
    phrase: str,
    variant_id: str | None,
    vehicle_id: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    specs = lookup_specs(phrase, variant_id, limit=limit, vehicle_id=vehicle_id)
    keyword = _keyword_chunks(phrase, limit=16, vehicle_id=vehicle_id)
    vector = _vector_chunks(phrase, limit=16, vehicle_id=vehicle_id)
    pages = _with_neighbours(_rrf_pages(keyword, vector, limit=limit))
    chunks = _whole_pages(vehicle_id, pages)
    manual = None
    if vehicle_id:
        from app.db import fetch_one

        manual = fetch_one(
            """
            SELECT v.make, v.model, v.year_from, v.year_to,
                   string_agg(DISTINCT d.doc_id, ', ') AS doc_ids,
                   string_agg(DISTINCT d.section_name, ', ') AS titles
              FROM vehicles v LEFT JOIN documents d ON d.vehicle_id = v.id
             WHERE v.id = %s GROUP BY v.id
            """,
            (vehicle_id,),
        )
    return {"mode": "hybrid", "specs": specs, "chunks": chunks, "query": phrase, "manual": dict(manual) if manual else None}
