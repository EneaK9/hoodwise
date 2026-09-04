"""Brand-agnostic owner/service-manual catalogs.

Drop a JSON file in ingestion/catalogs/ with make, folder, and a manuals list.
Ingest with: python -m ingestion.run_ingest --catalog --embed
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from app.db import get_conn

ROOT = Path(__file__).resolve().parents[1]
CATALOGS_DIR = Path(__file__).with_name("catalogs")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def catalog_files(explicit: Path | None = None) -> list[Path]:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            for candidate in (path, CATALOGS_DIR / path, CATALOGS_DIR / f"{path}.json"):
                if candidate.exists():
                    return [candidate]
        return [path]
    return sorted(CATALOGS_DIR.glob("*.json"))


def load_catalogs(explicit: Path | None = None) -> list[dict]:
    out: list[dict] = []
    for path in catalog_files(explicit):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        if not data.get("make"):
            raise ValueError(f"{path} missing make")
        out.append(data)
    return out


def url_stem(item: dict) -> str:
    stem = unquote((item.get("url") or item.get("file") or "").rsplit("/", 1)[-1])
    return re.sub(r"pdf$", "", stem, flags=re.I)


def file_slug(item: dict) -> str:
    text = f"{item['model']} {item['label']} {url_stem(item)}"
    return re.sub(r"[^\w]+", "_", text, flags=re.ASCII).strip("_")[:160] or "manual"


def make_prefix(make: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", make or "")
    return (letters[:2] or "OM").upper()


def doc_id_for(item: dict) -> str:
    make = item.get("make") or ""
    raw = f"{make_prefix(make)}_{item['model']}_{item['label']}_{url_stem(item)}"
    return re.sub(r"[^\w]+", "_", raw, flags=re.ASCII).strip("_")[:80]


def parse_years(label: str, now: int = 2026) -> tuple[int, int]:
    years = [int(m.group(0)) for m in YEAR_RE.finditer(label or "")]
    if not years:
        return 2000, now
    if len(years) == 1:
        if "present" in (label or "").lower():
            return years[0], now
        return years[0], years[0]
    return years[0], years[-1]


def normalize_model(model: str) -> str:
    model = re.sub(r"\s+", " ", (model or "").strip())
    if model.lower() == "n range":
        return "N Range"
    return model.title() if model.isupper() or model.islower() else model


def powertrain_label(label: str) -> str:
    low = (label or "").lower()
    if "plug-in" in low or "phev" in low:
        return "PHEV"
    if "hybrid" in low:
        return "Hybrid"
    if "electric" in low:
        return "Electric"
    if "avn" in low or "audio" in low:
        return "Infotainment"
    return "Owner manual"


def kind_phrase(kind: str | None) -> str:
    if not kind:
        return "manual"
    return kind.replace("_", " ")


def describe_item(item: dict, catalog: dict) -> dict:
    year_from, year_to = parse_years(item["label"])
    make = item.get("make") or catalog["make"]
    model = normalize_model(item["model"])
    kind = catalog.get("kind") or "manual"
    row = {
        **item,
        "make": make,
        "doc_id": doc_id_for({**item, "make": make}),
        "model": model,
        "label": item["label"],
        "section_name": f"{make} {model} {item['label']} {kind_phrase(kind)}",
        "year_from": year_from,
        "year_to": year_to,
        "engine_label": powertrain_label(item["label"]),
        "filename": f"{file_slug(item)}.pdf",
        "chassis": catalog.get("chassis") or "OM",
        "market": catalog.get("market") or "Any",
        "folder": catalog.get("folder") or "manuals",
        "kind": kind,
    }
    return row


def resolve_pdf(item: dict, folder: Path) -> Path | None:
    preferred = folder / f"{file_slug(item)}.pdf"
    if preferred.exists():
        return preferred
    named = item.get("file")
    if named and (folder / named).exists():
        return folder / named
    stem = url_stem(item)
    if not stem:
        return None
    fallbacks = list(folder.glob(f"*{stem}*.pdf"))
    return fallbacks[0] if fallbacks else None


def catalog_targets(explicit: Path | None = None) -> list[tuple[Path, dict]]:
    seen: set[str] = set()
    out: list[tuple[Path, dict]] = []
    for catalog in load_catalogs(explicit):
        folder = ROOT / catalog.get("folder", "manuals")
        for item in catalog.get("manuals") or []:
            path = resolve_pdf(item, folder)
            if not path:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append((path, describe_item(item, catalog)))
    out.sort(key=lambda pair: pair[1]["doc_id"])
    return out


def describe_pdf(pdf_path: Path) -> dict | None:
    name = pdf_path.name
    exact: list[dict] = []
    stem_hits: list[tuple[int, dict]] = []
    for catalog in load_catalogs():
        for item in catalog.get("manuals") or []:
            meta = describe_item(item, catalog)
            if name == meta["filename"] or name.lower() == f"{file_slug(item).lower()}.pdf":
                exact.append(meta)
                continue
            stem = url_stem(item).lower()
            folded = name.lower().replace(" ", "+")
            if stem and stem in folded:
                stem_hits.append((len(stem), meta))
    if exact:
        return exact[0]
    if not stem_hits:
        return None
    stem_hits.sort(key=lambda item: item[0], reverse=True)
    best_len = stem_hits[0][0]
    best = [meta for length, meta in stem_hits if length == best_len]
    return best[0] if len(best) == 1 else None


def ensure_catalog_document(meta: dict) -> str:
    """Upsert vehicle + generic variant + documents row for a catalog manual."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicles (make, model, generation, chassis, year_from, year_to)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (make, model, generation, chassis) DO UPDATE
                  SET year_from = EXCLUDED.year_from,
                      year_to = EXCLUDED.year_to
                RETURNING id
                """,
                (
                    meta["make"],
                    meta["model"],
                    meta["label"],
                    meta.get("chassis") or "OM",
                    meta["year_from"],
                    meta["year_to"],
                ),
            )
            vehicle_id = str(cur.fetchone()["id"])
            cur.execute(
                """
                SELECT id FROM vehicle_variants
                 WHERE vehicle_id = %s AND engine_label = %s
                 LIMIT 1
                """,
                (vehicle_id, meta["engine_label"]),
            )
            variant = cur.fetchone()
            if not variant:
                cur.execute(
                    """
                    INSERT INTO vehicle_variants
                      (vehicle_id, engine_code, engine_label, displacement_l, turbo,
                       transmission, trim, body, market, year_from, year_to)
                    VALUES (%s, NULL, %s, NULL, false, 'Any', NULL, NULL, %s, %s, %s)
                    """,
                    (
                        vehicle_id,
                        meta["engine_label"],
                        meta.get("market") or "Any",
                        meta["year_from"],
                        meta["year_to"],
                    ),
                )
            cur.execute(
                """
                INSERT INTO documents (vehicle_id, doc_id, filename, section_name, status)
                VALUES (%s, %s, %s, %s, 'pending')
                ON CONFLICT (doc_id) DO UPDATE
                  SET vehicle_id = EXCLUDED.vehicle_id,
                      filename = EXCLUDED.filename,
                      section_name = EXCLUDED.section_name
                """,
                (vehicle_id, meta["doc_id"], meta["filename"], meta["section_name"]),
            )
        conn.commit()
    return vehicle_id
