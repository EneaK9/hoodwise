"""Render a cited manual page clip. One page region, not the whole book."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from app.config import settings
from app.db import fetch_one

PAGE_MENTION = re.compile(
    r"\b(?:p(?:age)?s?\.?\s*)(\d{1,4}(?:\s*(?:,|and|&)\s*\d{1,4})*)",
    re.IGNORECASE,
)


def _pdf_for_doc(doc_id: str) -> Path | None:
    row = fetch_one("SELECT filename FROM documents WHERE doc_id = %s", (doc_id,))
    filename = (row or {}).get("filename") or f"{doc_id}.pdf"
    roots = [
        Path(settings.manual_dir),
        Path("Hyundai Owners Manuals"),
        Path("manuals"),
    ]
    try:
        from ingestion.catalog import load_catalogs

        for catalog in load_catalogs():
            folder = catalog.get("folder")
            if folder:
                roots.append(Path(folder))
    except Exception:
        pass
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        direct = root / filename
        if direct.is_file():
            return direct
        stem = Path(filename).stem
        hits = list(root.glob(f"*{stem}*.pdf")) if stem else []
        if hits:
            return hits[0]
    return None


def _clip_around_hits(page: pymupdf.Page, needles: list[str]) -> pymupdf.Rect:
    hits: list[pymupdf.Rect] = []
    for needle in needles:
        if len(needle) < 3:
            continue
        try:
            hits.extend(page.search_for(needle, quads=False) or [])
        except Exception:
            continue
    box = page.rect
    if not hits:
        return box
    clip = hits[0]
    for rect in hits[1:6]:
        clip = clip | rect
    pad = 48
    clip = pymupdf.Rect(
        max(box.x0, clip.x0 - pad),
        max(box.y0, clip.y0 - pad),
        min(box.x1, clip.x1 + pad),
        min(box.y1, clip.y1 + pad * 3),
    )
    if clip.height < 160:
        clip.y1 = min(box.y1, clip.y0 + 220)
    if clip.height > box.height * 0.7:
        return box
    return clip


def render_snippet(doc_id: str, page_number: int, needles: list[str] | None = None) -> str | None:
    if not doc_id or not page_number or page_number < 1:
        return None
    pdf_path = _pdf_for_doc(doc_id)
    if not pdf_path:
        return None
    out_dir = Path(settings.data_dir) / "snippets" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"p{page_number:04d}.png"
    if dest.exists() and dest.stat().st_size > 500:
        return str(dest)
    try:
        document = pymupdf.open(pdf_path)
    except Exception:
        return None
    try:
        if page_number > document.page_count:
            return None
        page = document[page_number - 1]
        clip = _clip_around_hits(page, needles or [])
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), clip=clip, alpha=False)
        pix.save(str(dest))
    except Exception:
        return None
    finally:
        document.close()
    return str(dest) if dest.exists() else None


def snippet_needles(question: str, answer: str = "") -> list[str]:
    """What to highlight on the page: the figures the answer quotes first (they are the
    point of the citation), then the longer words of the question. No stop list."""
    out: list[str] = []
    for num in re.findall(r"\d+(?:[.,]\d+)?", answer or ""):
        if len(num) > 1 and num not in out:
            out.append(num)
    for word in re.findall(r"[A-Za-z]{5,}", question or ""):
        if word.lower() not in {w.lower() for w in out}:
            out.append(word)
    return out[:8]


def pages_from_answer(answer: str) -> list[int]:
    found: list[int] = []
    for match in PAGE_MENTION.finditer(answer or ""):
        for num in re.findall(r"\d+", match.group(1)):
            page = int(num)
            if 1 <= page <= 2000 and page not in found:
                found.append(page)
    return found[:3]


def attach_snippets(
    citations: list[dict],
    question: str,
    answer: str,
    retrieved: dict,
) -> list[dict]:
    needles = snippet_needles(question, answer)
    cited_pages = pages_from_answer(answer)
    chunks = retrieved.get("chunks") or []
    doc_id = None
    for chunk in chunks:
        if chunk.get("doc_id"):
            doc_id = chunk["doc_id"]
            break
    if not citations and doc_id:
        pages = cited_pages or [
            int(c["page_number"]) for c in chunks if c.get("page_number")
        ][:2]
        for page in pages:
            citations.append(
                {
                    "kind": "page",
                    "doc_id": doc_id,
                    "page_number": page,
                    "section_name": next(
                        (c.get("section_name") for c in chunks if c.get("page_number") == page),
                        None,
                    ),
                    "part_name": "Manual page",
                    "value_raw": f"p.{page}",
                    "crop_path": None,
                }
            )
    for row in citations:
        page = row.get("page_number")
        did = row.get("doc_id") or doc_id
        if not page or not did:
            continue
        path = render_snippet(str(did), int(page), needles)
        if path:
            row["crop_path"] = path
    return citations
