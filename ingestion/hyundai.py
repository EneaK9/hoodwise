"""Hyundai UK owner-manual catalog: paths, vehicles, document ids."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from app.db import get_conn

CATALOG_PATH = Path(__file__).with_name("hyundai_uk_owners_manuals.json")
MANUAL_DIR = Path(__file__).resolve().parents[1] / "Hyundai Owners Manuals"
YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def url_stem(item: dict) -> str:
    stem = unquote(item["url"].rsplit("/", 1)[-1])
    return re.sub(r"pdf$", "", stem, flags=re.I)


def file_slug(item: dict) -> str:
    text = f"{item['model']} {item['label']} {url_stem(item)}"
    return re.sub(r"[^\w]+", "_", text, flags=re.ASCII).strip("_")[:160] or "manual"


def doc_id_for(item: dict) -> str:
    raw = f"HY_{item['model']}_{item['label']}_{url_stem(item)}"
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


def describe_item(item: dict) -> dict:
    year_from, year_to = parse_years(item["label"])
    model = normalize_model(item["model"])
    return {
        "doc_id": doc_id_for(item),
        "model": model,
        "label": item["label"],
        "section_name": f"Hyundai {model} {item['label']} owner manual",
        "year_from": year_from,
        "year_to": year_to,
        "engine_label": powertrain_label(item["label"]),
        "filename": f"{file_slug(item)}.pdf",
    }


def resolve_pdf(item: dict, folder: Path | None = None) -> Path | None:
    folder = folder or MANUAL_DIR
    preferred = folder / f"{file_slug(item)}.pdf"
    if preferred.exists():
        return preferred
    fallbacks = list(folder.glob(f"*{url_stem(item)}*.pdf"))
    return fallbacks[0] if fallbacks else None


def catalog_targets(folder: Path | None = None) -> list[tuple[Path, dict]]:
    folder = folder or MANUAL_DIR
    seen: set[str] = set()
    out: list[tuple[Path, dict]] = []
    for item in load_catalog()["manuals"]:
        path = resolve_pdf(item, folder)
        if not path:
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append((path, describe_item(item)))
    out.sort(
        key=lambda pair: (
            0 if "santa" in pair[1]["model"].lower() and "2016" in pair[1]["label"] else 1,
            pair[1]["doc_id"],
        )
    )
    return out


def describe_pdf(pdf_path: Path) -> dict | None:
    name = pdf_path.name
    for item in load_catalog()["manuals"]:
        meta = describe_item(item)
        if name == meta["filename"] or url_stem(item).lower() in name.lower().replace(" ", "+"):
            return meta
        if name.lower() == f"{file_slug(item).lower()}.pdf":
            return meta
    return None


def ensure_hyundai_document(meta: dict) -> str:
    """Upsert vehicle + generic variant + documents row for an owner manual."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicles (make, model, generation, chassis, year_from, year_to)
                VALUES ('Hyundai', %s, %s, 'UK-OM', %s, %s)
                ON CONFLICT (make, model, generation, chassis) DO UPDATE
                  SET year_from = EXCLUDED.year_from,
                      year_to = EXCLUDED.year_to
                RETURNING id
                """,
                (meta["model"], meta["label"], meta["year_from"], meta["year_to"]),
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
                    VALUES (%s, NULL, %s, NULL, false, 'Any', NULL, NULL, 'UK', %s, %s)
                    """,
                    (vehicle_id, meta["engine_label"], meta["year_from"], meta["year_to"]),
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
