"""Read a maker's VIN key out of a workshop manual and store it as structured rows.

Workshop manuals carry an "Identification Number Description" section that explains what
each VIN position means for that model line: vehicle line codes, grade, body, restraint,
engine type, drive side and transmission, year, plant. The model reads those pages and
returns rows; nothing is hand-typed. Usage:

    python -m ingestion.vin_keys --pdf path/to/service_manual.pdf --make Hyundai --source-doc "Santa Fe DM service manual" [--source-url URL]
    python -m ingestion.vin_keys --text pages.txt --make Hyundai --source-doc "..." --page 7
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.db import get_conn

FieldName = Literal["make", "model", "grade", "body", "restraint", "engine", "drive_transmission", "check_digit", "year", "plant", "serial", "other"]
Scope = Literal["all", "north_america", "outside_north_america"]


class KeyEntry(BaseModel):
    code: str | None = Field(None, description="The character(s) at this position, exactly as printed; null for a range like the serial")
    value: str = Field(description="What the code means, as a short display value (e.g. 'Santa Fe', 'High grade (TOP)', 'Wagon', 'LHD & AT', '2016', 'Ulsan (Korea)')")
    market: str | None = Field(None, description="The market condition as printed, e.g. 'Except U.S.A, Canada, Mexico'")
    market_scope: Scope = Field("all", description="north_america if the condition applies to USA/Canada/Mexico, outside_north_america if it excludes them, else all")
    transmission: str | None = Field(None, description="If the code encodes a transmission: MT, AT, CVT, DCT...")
    drive_side: str | None = Field(None, description="If the code encodes the steering side: LHD or RHD")
    fuel: Literal["diesel", "gasoline", "hybrid", "electric"] | None = Field(None, description="If the code is an engine, its fuel")
    displacement_l: str | None = Field(None, description="If the code is an engine, its displacement in litres like '2.0'")
    engine_family: str | None = Field(None, description="If the code is an engine, the family name as printed (e.g. 'Theta-II GDI', 'R 2.0 CRDi')")


class KeyPosition(BaseModel):
    position_from: int = Field(ge=1, le=17)
    position_to: int = Field(ge=1, le=17)
    field: FieldName
    entries: list[KeyEntry]


class VinKey(BaseModel):
    make: str
    model_lines: list[str] = Field(description="Display names of the model lines this key covers")
    wmis: list[str] = Field(description="Every 3-character WMI listed")
    positions: list[KeyPosition]
    notes: str | None = None


SYSTEM = """You read the "Identification Number Description" section of a car maker's workshop manual and
return its VIN key as structured rows. The manual numbers its items (1. WMI, 2. Vehicle line, ...);
map each item to actual VIN character positions: WMI is positions 1-3, then each following item is
one position (4, 5, 6, 7, 8, 9, 10, 11), and the production sequence is positions 12-17. Keep every
code exactly as printed, including digits. Keep market conditions and classify their scope. When a
code encodes two things (e.g. "LHD & AT") fill both transmission and drive_side. For engine codes
fill fuel, displacement and family from the printed text only. Do not invent codes that are not on
the pages."""


def find_key_pages(pdf_path: Path, max_pages: int = 60) -> list[int]:
    import pymupdf

    doc = pymupdf.open(pdf_path)
    hits = []
    for i in range(min(doc.page_count, max_pages)):
        t = doc[i].get_text()
        if re.search(r"identification number", t, re.I) and re.search(r"WMI|World Manufacturer", t, re.I):
            hits.append(i)
    if hits:
        first = hits[0]
        return list(range(first, min(first + 4, doc.page_count)))
    return []


def pages_text(pdf_path: Path, pages: list[int]) -> str:
    import pymupdf

    doc = pymupdf.open(pdf_path)
    return "\n\n".join(f"===== page {i + 1} =====\n{doc[i].get_text()}" for i in pages)


def extract_key(text: str, make: str) -> VinKey | None:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.parse(
        model=settings.understand_model,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"MAKE: {make}\n\nPAGES:\n{text[:60000]}"}],
        output_format=VinKey,
        output_config={"effort": "medium"},
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


def store_key(key: VinKey, source_doc: str, source_page: int | None, source_url: str | None) -> int:
    rows = []
    model_line = ", ".join(key.model_lines) if key.model_lines else None
    for pos in key.positions:
        for e in pos.entries:
            attrs = {k: v for k, v in (
                ("transmission", e.transmission), ("drive_side", e.drive_side), ("fuel", e.fuel),
                ("displacement_l", e.displacement_l), ("engine_family", e.engine_family),
            ) if v}
            rows.append((
                key.make, model_line, [w.upper() for w in key.wmis], pos.position_from, pos.position_to,
                pos.field, e.code, e.value, e.market, e.market_scope, json.dumps(attrs) if attrs else None,
                source_doc, source_page, source_url,
            ))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vin_keys WHERE source_doc = %s", (source_doc,))
            cur.executemany(
                """
                INSERT INTO vin_keys (make, model_line, wmis, position_from, position_to, field, code, value,
                                      market, market_scope, attrs, source_doc, source_page, source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--text", type=Path)
    ap.add_argument("--make", required=True)
    ap.add_argument("--source-doc", required=True)
    ap.add_argument("--source-url")
    ap.add_argument("--page", type=int)
    args = ap.parse_args()
    if args.pdf:
        pages = find_key_pages(args.pdf)
        if not pages:
            print("no identification-number pages found")
            return 1
        text = pages_text(args.pdf, pages)
        page = pages[0] + 1
    elif args.text:
        text = args.text.read_text(encoding="utf-8")
        page = args.page
    else:
        ap.error("--pdf or --text required")
        return 2
    key = extract_key(text, args.make)
    if not key:
        print("extraction refused/failed")
        return 1
    n = store_key(key, args.source_doc, page, args.source_url)
    print(f"stored {n} key rows for {key.make} {key.model_lines} WMIs={key.wmis}")
    for pos in key.positions:
        print(f"  pos {pos.position_from}-{pos.position_to} {pos.field}: {len(pos.entries)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
