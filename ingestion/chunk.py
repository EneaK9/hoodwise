"""Section-aware chunking from extracted pages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ingestion.extract import ExtractedDocument

HEADING_RE = re.compile(r"^[A-Z][A-Za-z0-9 /&().'™-]{8,120}$")
MAX_CHARS = 1800


@dataclass
class TextChunk:
    page_number: int
    section_path: str
    chunk_type: str
    content: str


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.lower() in {"cardiagn.com"}:
        return False
    if stripped.startswith("-- ") and " of " in stripped:
        return False
    return bool(HEADING_RE.match(stripped)) and not stripped.endswith(".")


def chunk_document(doc: ExtractedDocument) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current_heading = doc.section_name
    buf: list[str] = []
    buf_page = 1

    def flush() -> None:
        nonlocal buf, buf_page
        text = "\n".join(buf).strip()
        if len(text) < 40:
            buf = []
            return
        chunks.append(
            TextChunk(
                page_number=buf_page,
                section_path=current_heading,
                chunk_type="prose",
                content=text[:4000],
            )
        )
        buf = []

    for page in doc.pages:
        for line in page.text.splitlines():
            if _is_heading(line):
                flush()
                current_heading = line.strip()
                buf_page = page.page_number
                continue
            if not buf:
                buf_page = page.page_number
            buf.append(line)
            if sum(len(x) for x in buf) >= MAX_CHARS:
                flush()
        if page.render_path and page.char_count < 20:
            chunks.append(
                TextChunk(
                    page_number=page.page_number,
                    section_path=current_heading,
                    chunk_type="diagram_caption",
                    content=f"[{doc.section_name}] diagram-only page {page.page_number}",
                )
            )
    flush()
    return chunks
