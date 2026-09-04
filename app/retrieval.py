"""Hybrid retrieval: spec SQL + pgvector/tsvector reciprocal rank fusion."""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.db import fetch_all, fetch_one
from app.resolve import PART_PHRASES
from ingestion.embed import embed_texts


def _spec_blob(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in ("part_name", "raw_context", "condition_note", "value_raw", "section_name")
    ).lower()


def filter_specs_for_hints(rows: list[dict[str, Any]], hints: list[str]) -> list[dict[str, Any]]:
    if not hints or not rows:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        blob = _spec_blob(row)
        cond = (row.get("condition_note") or "").lower()
        if "typer" in hints and "except type" in blob:
            continue
        if "si" in hints and "except si" in blob:
            continue
        if "typer" in hints and "type-r" not in blob and "type r" not in blob and "2.0" in hints:
            # Type-R still sees generic rows that have no variant tag
            pass
        if "1.5" in hints and "2.0" not in hints and re.search(r"2\.0\s*l", blob) and "1.5" not in cond:
            if "1.5" not in blob:
                continue
        if "2.0" in hints and "1.5" not in hints and re.search(r"1\.5\s*l", cond) and "2.0" not in blob:
            continue
        kept.append(row)
    return kept or rows

NUMERIC_HINT = re.compile(
    r"\b(torque|n[·.]?m|lbf|capacity|clearance|spec|ft-?lb|tighten|oil|litre|liter|quart|fluid)\b",
    re.IGNORECASE,
)


def is_numeric_query(question: str) -> bool:
    return bool(NUMERIC_HINT.search(question))


def _keywords(question: str) -> list[str]:
    stop = {
        "what", "is", "the", "a", "an", "for", "on", "of", "to", "in", "and",
        "or", "how", "do", "does", "with", "from", "this", "that", "these",
        "those", "my", "your", "car", "take", "much", "please", "need",
        "want", "tell", "me", "it", "its", "about", "many",
    }
    words = re.findall(r"[A-Za-z0-9./-]+", question.lower())
    return [w for w in words if w not in stop and len(w) > 1][:8]


def _ts_or_query(question: str) -> str:
    keys = [re.sub(r"[^a-z0-9]+", "", k) for k in _keywords(question)]
    keys = [k for k in keys if k]
    return " | ".join(keys) if keys else "oil"


def search_vehicle_id(vehicle_id: str | None) -> str | None:
    if vehicle_id:
        return vehicle_id
    row = fetch_one("SELECT id FROM vehicles WHERE make = 'Honda' AND model = 'Civic' LIMIT 1")
    return str(row["id"]) if row else None


def lookup_specs(
    question: str,
    variant_id: str | None,
    limit: int = 8,
    vehicle_id: str | None = None,
) -> list[dict[str, Any]]:
    keys = _keywords(question)
    like_terms = [f"%{k}%" for k in keys[:8]] or ["%torque%"]
    vid = search_vehicle_id(vehicle_id)
    sql = """
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
               OR coalesce(s.condition_note,'') ILIKE ANY(%s)
               OR s.value_raw ILIKE ANY(%s)
              )
           AND (s.variant_id IS NULL OR %s::uuid IS NULL OR s.variant_id = %s::uuid)
           AND (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
         ORDER BY s.verification_status = 'flagged', s.page_number
         LIMIT %s
    """
    rows = fetch_all(
        sql,
        (
            question,
            like_terms,
            like_terms,
            like_terms,
            like_terms,
            variant_id,
            variant_id,
            vid,
            vid,
            limit * 4,
        ),
    )
    scored: list[tuple[int, dict]] = []
    for row in rows:
        blob = _spec_blob(row)
        score = sum(1 for k in keys if k in blob)
        qlow = question.lower()
        for phrase in PART_PHRASES:
            if phrase in qlow and phrase in blob:
                score += 3
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _keyword_chunks(question: str, limit: int = 12, vehicle_id: str | None = None) -> list[dict[str, Any]]:
    vid = search_vehicle_id(vehicle_id)
    ts_q = _ts_or_query(question)
    likes = [f"%{k}%" for k in _keywords(question)[:8]] or ["%oil%"]
    return fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content,
               d.doc_id, d.section_name,
               ts_rank_cd(c.tsv, to_tsquery('english', %s)) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE (
                 c.tsv @@ to_tsquery('english', %s)
              OR c.content ILIKE ANY(%s)
              )
           AND (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
         ORDER BY rank DESC NULLS LAST
         LIMIT %s
        """,
        (ts_q, ts_q, likes, vid, vid, limit),
    )


def _vector_chunks(question: str, limit: int = 12, vehicle_id: str | None = None) -> list[dict[str, Any]]:
    if not settings.openai_api_key:
        return []
    try:
        vec = embed_texts([question])[0]
    except Exception:
        return []
    vid = search_vehicle_id(vehicle_id)
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
        (vec, vid, vid, vec, limit),
    )


def _rrf(keyword: list[dict], vector: list[dict], k: int = 60, limit: int = 8) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for rank, row in enumerate(keyword, start=1):
        cid = str(row["id"])
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        rows[cid] = row
    for rank, row in enumerate(vector, start=1):
        cid = str(row["id"])
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        rows[cid] = row
    ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:limit]
    out = []
    for cid in ordered:
        row = dict(rows[cid])
        row["rrf"] = scores[cid]
        out.append(row)
    return out


def search_chunks(
    question: str,
    limit: int = 8,
    vehicle_id: str | None = None,
    hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    keyword = _keyword_chunks(question, limit=12, vehicle_id=vehicle_id)
    vector = _vector_chunks(question, limit=12, vehicle_id=vehicle_id)
    if not vector:
        merged = keyword[:limit]
    elif not keyword:
        merged = vector[:limit]
    else:
        merged = _rrf(keyword, vector, limit=limit)
    if _is_capacity_query(question):
        merged = _prefer_capacity_chunks(
            merged, vehicle_id=vehicle_id, limit=limit, hints=hints
        )
    return merged


LITRE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*l\b", re.IGNORECASE)
CAPACITY_HINT = re.compile(
    r"\b(oil|coolant|capacit|lubricant|fluid|litre|liter|quart|fuel)\b",
    re.IGNORECASE,
)


def _is_capacity_query(question: str) -> bool:
    q = question.lower()
    return bool(CAPACITY_HINT.search(q)) and bool(
        re.search(r"\b(how much|capacit|take|fill|litre|liter|quart|oil|coolant|fuel)\b", q)
    )


def _capacity_chunks(vehicle_id: str | None, limit: int = 40) -> list[dict[str, Any]]:
    vid = search_vehicle_id(vehicle_id)
    return fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content,
               d.doc_id, d.section_name
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
           AND (
                 c.content ILIKE '%%US qt%%'
              OR c.content ~* '[0-9]+([.,][0-9]+)?\\s*l\\s*\\('
               )
         ORDER BY
           (c.content ILIKE '%%engine oil%%')::int DESC,
           (c.content ILIKE '%%US qt%%')::int DESC,
           c.page_number,
           c.id
         LIMIT %s
        """,
        (vid, vid, limit),
    )


def _merge_page_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, Any], dict[str, Any]] = {}
    order: list[tuple[Any, Any]] = []
    for chunk in chunks:
        key = (chunk.get("doc_id"), chunk.get("page_number"))
        if key not in groups:
            groups[key] = dict(chunk)
            order.append(key)
            continue
        existing = groups[key].get("content") or ""
        extra = chunk.get("content") or ""
        if extra and extra not in existing:
            groups[key]["content"] = (existing + "\n" + extra)[:4000]
    return [groups[key] for key in order]


def _chunks_on_pages(
    vehicle_id: str | None, pages: list[int], doc_ids: list[str]
) -> list[dict[str, Any]]:
    if not pages:
        return []
    vid = search_vehicle_id(vehicle_id)
    return fetch_all(
        """
        SELECT c.id, c.page_number, c.section_path, c.chunk_type, c.content,
               d.doc_id, d.section_name
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE (%s::uuid IS NULL OR d.vehicle_id = %s::uuid)
           AND c.page_number = ANY(%s)
           AND (%s::text[] IS NULL OR d.doc_id = ANY(%s))
         ORDER BY c.page_number, c.id
        """,
        (vid, vid, pages, doc_ids or None, doc_ids or None),
    )


def score_capacity_chunk(text: str, hints: list[str] | None = None) -> int:
    hints = hints or []
    score = 3
    diesel_row = bool(re.search(r"\b(diesel|dpf|crdi)\b", text, re.I))
    gas_row = bool(re.search(r"\b(gasoline|petrol)\b", text, re.I))
    if re.search(r"engine oil", text, re.I):
        score += 6
    if re.search(r"API Service|ILSAC|ACEA", text, re.I):
        score += 3
    if re.search(r"lubricant", text, re.I):
        score += 1
    if diesel_row:
        score += 4
    if "diesel" in hints:
        if diesel_row:
            score += 8
        elif gas_row:
            score -= 6
    if "gasoline" in hints:
        if gas_row:
            score += 8
        elif diesel_row:
            score -= 6
    if "2.0" in hints:
        if diesel_row:
            score += 4
        if re.search(r"2\.4\s*l", text, re.I) and not re.search(r"2\.0", text, re.I):
            score -= 3
    return score


def _prefer_capacity_chunks(
    chunks: list[dict[str, Any]],
    vehicle_id: str | None,
    limit: int,
    hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    volume_hits = _capacity_chunks(vehicle_id)
    pages = sorted({int(row["page_number"]) for row in volume_hits if row.get("page_number") is not None})
    doc_ids = sorted({str(row["doc_id"]) for row in volume_hits if row.get("doc_id")})
    page_chunks = _chunks_on_pages(vehicle_id, pages, doc_ids) if pages else []
    combined = _merge_page_chunks(page_chunks + volume_hits + list(chunks))
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in combined:
        text = row.get("content") or ""
        has_vol = bool(LITRE_RE.search(text) or re.search(r"US qt", text, re.I))
        if not has_vol:
            continue
        score = score_capacity_chunk(text, hints or [])
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    preferred = [row for _, row in scored[:limit]]
    return preferred or chunks[:limit]


def retrieve(
    question: str,
    variant_id: str | None,
    hints: list[str] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    numeric = is_numeric_query(question)
    specs = lookup_specs(question, variant_id, limit=8 if numeric else 4, vehicle_id=vehicle_id)
    specs = filter_specs_for_hints(specs, hints or [])
    chunks = search_chunks(question, vehicle_id=vehicle_id, hints=hints)
    mode = "sql_spec" if numeric and specs else "hybrid"
    return {"mode": mode, "numeric": numeric, "specs": specs, "chunks": chunks}
