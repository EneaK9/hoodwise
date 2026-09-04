"""Resolve VIN + question into a vehicle profile and spec-filter hints."""

from __future__ import annotations

import re
from typing import Any

from app.vin import decode_vin, find_vins

HINT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("typer", re.compile(r"type-?r", re.I)),
    ("si", re.compile(r"\bsi\b", re.I)),
    ("diesel", re.compile(r"\b(diesel|crdi|tdi|cdti|dci|hdi|tdci)\b", re.I)),
    ("gasoline", re.compile(r"\b(gasoline|petrol|gdi|t-?gdi)\b", re.I)),
    ("1.5", re.compile(r"1\.5|l15", re.I)),
    ("2.0", re.compile(r"2\.0|k20", re.I)),
]

PART_PHRASES = (
    "front brake hose",
    "rear brake hose",
    "brake hose",
    "banjo bolt",
    "bleed screw",
    "brake line",
    "master cylinder",
    "vsa modulator",
    "spindle nut",
    "axle nut",
    "oil filter",
    "camshaft holder",
    "vtc actuator",
    "connecting rod",
)


def infer_hints_from_text(text: str) -> list[str]:
    hints: list[str] = []
    for name, pat in HINT_PATTERNS:
        if pat.search(text or ""):
            hints.append(name)
    return hints


def apply_manual_fuel(vehicle: dict[str, Any] | None, retrieved: dict[str, Any] | None) -> None:
    """If the VIN has no fuel, pin it when a labeled oil row matches displacement."""
    if not vehicle or not retrieved:
        return
    if vehicle.get("fuel") in {"diesel", "gasoline"}:
        return
    text = "\n".join(
        str(chunk.get("content") or "") for chunk in retrieved.get("chunks") or []
    )
    disp = str(vehicle.get("displacement_l") or vehicle.get("engine_label") or "")
    disp_m = re.search(r"(\d+\.\d+)", disp)
    if not disp_m or not re.search(r"\bdiesel\b", text, re.I):
        return
    num = re.escape(disp_m.group(1))
    labeled = bool(re.search(rf"diesel.{{0,80}}{num}|{num}\s*/\s*\d+\.\d+", text, re.I))
    dpf_row = bool(re.search(r"diesel engine with dpf", text, re.I))
    gas_same = bool(re.search(rf"gasoline.{{0,40}}{num}", text, re.I))
    if not ((labeled or dpf_row) and not gas_same):
        return
    vehicle["fuel"] = "diesel"
    label = vehicle.get("label") or ""
    if label and "diesel" not in label.lower():
        vehicle["label"] = f"{label} diesel"


def infer_fuel(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    from app.vin import _fuel_from_text

    explicit = decoded.get("fuel")
    if explicit in {"diesel", "gasoline"}:
        return explicit
    if isinstance(explicit, str):
        hit = _fuel_from_text(explicit)
        if hit:
            return hit
    bits = [
        str(decoded.get(k) or "")
        for k in ("fuel", "engine_label", "engine_code", "trim")
    ]
    for spec in decoded.get("specs") or []:
        bits.append(str(spec.get("label") or ""))
        bits.append(str(spec.get("value") or ""))
    return _fuel_from_text(" ".join(bits))


def infer_hints_from_decode(decoded: dict[str, Any] | None) -> list[str]:
    if not decoded:
        return []
    blob = " ".join(
        str(decoded.get(k) or "")
        for k in ("fuel", "engine_label", "engine_code", "trim", "transmission", "body")
    )
    hints = infer_hints_from_text(blob)
    fuel = infer_fuel(decoded)
    if fuel and fuel not in hints:
        hints.append(fuel)
    return hints


def vehicle_label(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    bits = [
        str(decoded.get("year") or ""),
        str(decoded.get("make") or ""),
        str(decoded.get("model") or ""),
        str(decoded.get("trim") or ""),
        str(decoded.get("body") or "").split("/")[0],
        str(decoded.get("engine_label") or ""),
        str(infer_fuel(decoded) or decoded.get("fuel") or ""),
        str(decoded.get("transmission") or ""),
    ]
    skip = {"none", "any", "n/a", "unknown", "null"}
    label = " ".join(b for b in bits if b and b.lower() not in skip).strip()
    if label and not decoded.get("model"):
        label = f"{label} (model unconfirmed)"
    return label or None


def clean_part_name(part_name: str, raw_context: str = "") -> str:
    blob = f"{raw_context} {part_name}".lower()
    hits = [(blob.rfind(p), p) for p in PART_PHRASES if p in blob]
    if hits:
        return max(hits, key=lambda item: (item[0], len(item[1])))[1]
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*n[·.]?m.*", "", part_name, flags=re.I)
    cleaned = re.sub(r"(specified torque|value note|tighten[:\s]*)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|")
    return cleaned[-80:] or "fastener"


def _enrich_from_variant(decoded: dict[str, Any]) -> dict[str, Any]:
    variant_id = decoded.get("variant_id")
    if not variant_id:
        return decoded
    from app.db import fetch_one

    row = fetch_one(
        """
        SELECT engine_label, engine_code, transmission, trim, body
          FROM vehicle_variants WHERE id = %s
        """,
        (variant_id,),
    )
    if not row:
        return decoded
    for key in ("engine_label", "engine_code", "transmission", "trim", "body"):
        if not decoded.get(key) and row.get(key):
            decoded[key] = row[key]
    return decoded


def public_vehicle(decoded: dict[str, Any] | None) -> dict[str, Any] | None:
    if not decoded:
        return None
    decoded = _enrich_from_variant(dict(decoded))
    fuel = infer_fuel(decoded)
    if fuel:
        decoded["fuel"] = fuel
    return {
        "year": decoded.get("year"),
        "make": decoded.get("make"),
        "model": decoded.get("model"),
        "trim": decoded.get("trim"),
        "body": decoded.get("body"),
        "engine_label": decoded.get("engine_label"),
        "engine_code": decoded.get("engine_code"),
        "fuel": fuel,
        "transmission": decoded.get("transmission"),
        "variant_id": decoded.get("variant_id"),
        "source": decoded.get("source"),
        "confidence": decoded.get("confidence"),
        "note": decoded.get("note"),
        "label": vehicle_label(decoded),
        "specs": decoded.get("specs") or [],
        "displacement_l": decoded.get("displacement_l"),
        "plant_country": decoded.get("plant_country"),
        "history": decoded.get("history"),
    }


def resolve_vehicle(message: str, vin: str | None, session_vin: str | None = None) -> dict[str, Any]:
    found = find_vins(message)
    chosen = (vin or (found[0] if found else None) or session_vin or "").strip().upper() or None
    decoded = None
    if chosen:
        try:
            decoded = decode_vin(chosen)
            if decoded:
                decoded = _enrich_from_variant(decoded)
        except ValueError:
            decoded = None
            chosen = None
    hints = infer_hints_from_decode(decoded) + infer_hints_from_text(message)
    # de-dupe, keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return {
        "vin": chosen,
        "decoded": decoded,
        "hints": ordered,
        "variant_id": (decoded or {}).get("variant_id"),
        "vehicle_id": _vehicle_id_for_decode(decoded),
        "label": vehicle_label(decoded),
    }


def _vehicle_id_for_decode(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    from app.db import fetch_one

    variant_id = decoded.get("variant_id")
    if variant_id:
        row = fetch_one("SELECT vehicle_id FROM vehicle_variants WHERE id = %s", (variant_id,))
        if row and row.get("vehicle_id"):
            return str(row["vehicle_id"])
    make = decoded.get("make")
    model = decoded.get("model")
    year = decoded.get("year")
    if not (make and model):
        return None
    if year:
        row = fetch_one(
            """
            SELECT id FROM vehicles
             WHERE lower(make) = lower(%s)
               AND lower(model) = lower(%s)
               AND year_from <= %s AND year_to >= %s
             ORDER BY year_from DESC
             LIMIT 1
            """,
            (make, model, year, year),
        )
    else:
        row = fetch_one(
            """
            SELECT id FROM vehicles
             WHERE lower(make) = lower(%s) AND lower(model) = lower(%s)
             LIMIT 1
            """,
            (make, model),
        )
    return str(row["id"]) if row else None
