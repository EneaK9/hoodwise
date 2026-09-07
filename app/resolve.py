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

NOT_AN_ENGINE = {"owner manual", "infotainment", "owner's manual", "navigation"}

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


_DIESEL_ROW = re.compile(r"\bdiesel\s+engine(?:\s+with\s+dpf)?|\bdpf\b|\bcrdi\b", re.I | re.S)


def manual_fuel_from_text(text: str, displacement: str | None) -> str | None:
    """Diesel only if the manual labels a diesel row for this engine size.

    Table text arrives with newlines between words ("Diesel\\nEngine\\nwith DPF"), so
    every gap is matched with \\s+. Returns None when a petrol row shares the size.
    """
    if not text or not re.search(r"\bdiesel\b", text, re.I):
        return None
    disp_m = re.search(r"(\d+\.\d+)", str(displacement or ""))
    if not disp_m:
        return None
    num = re.escape(disp_m.group(1))
    labeled = bool(
        re.search(rf"diesel.{{0,80}}{num}|\bR{num}\b|{num}\s*/\s*\d+\.\d+", text, re.I | re.S)
    )
    dpf_row = bool(_DIESEL_ROW.search(text))
    gas_same = bool(re.search(rf"(gasoline|petrol).{{0,40}}{num}\s*L?\b", text, re.I | re.S))
    if (labeled or dpf_row) and not gas_same:
        return "diesel"
    return None


def apply_manual_fuel(vehicle: dict[str, Any] | None, retrieved: dict[str, Any] | None) -> None:
    """Last resort: if the VIN has no fuel, pin it from a labeled manual row."""
    if not vehicle or not retrieved:
        return
    if vehicle.get("fuel") in {"diesel", "gasoline"}:
        return
    text = "\n".join(
        str(chunk.get("content") or "") for chunk in retrieved.get("chunks") or []
    )
    disp = str(vehicle.get("displacement_l") or vehicle.get("engine_label") or "")
    fuel = manual_fuel_from_text(text, disp)
    if not fuel:
        return
    vehicle["fuel"] = fuel
    vehicle["fuel_source"] = "manual"
    vehicle["needs_fuel_confirmation"] = False
    label = vehicle.get("label") or ""
    if label and fuel not in label.lower():
        vehicle["label"] = f"{label} {fuel}"


def infer_fuel(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    from app.vin import infer_engine_identity

    return infer_engine_identity(decoded).get("fuel")


def displacement_hint(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    for key in ("displacement_l", "engine_label", "engine_family"):
        hit = re.search(r"(\d\.\d)", str(decoded.get(key) or ""))
        if hit:
            return hit.group(1)
    cc = decoded.get("displacement_cc")
    if cc:
        try:
            return f"{int(cc) / 1000:.1f}"
        except (TypeError, ValueError):
            return None
    return None


def infer_hints_from_decode(decoded: dict[str, Any] | None) -> list[str]:
    if not decoded:
        return []
    blob = " ".join(
        str(decoded.get(k) or "")
        for k in ("fuel", "engine_label", "engine_family", "engine_code", "trim", "transmission", "body")
    )
    hints = infer_hints_from_text(blob)
    fuel = infer_fuel(decoded)
    if fuel and fuel not in hints:
        hints.append(fuel)
    disp = displacement_hint(decoded)
    if disp and disp not in hints:
        hints.append(disp)
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
    skip = {"none", "any", "n/a", "unknown", "null"} | NOT_AN_ENGINE
    seen: set[str] = set()
    kept: list[str] = []
    for bit in bits:
        low = bit.lower()
        if not bit or low in skip or low in seen:
            continue
        seen.add(low)
        kept.append(bit)
    label = " ".join(kept).strip()
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
        value = row.get(key)
        if key == "engine_label" and str(value or "").lower() in NOT_AN_ENGINE:
            continue  # catalog placeholders ("Owner manual"), not engines
        if not decoded.get(key) and value:
            decoded[key] = value
    return decoded


def public_vehicle(decoded: dict[str, Any] | None) -> dict[str, Any] | None:
    if not decoded:
        return None
    from app.vin import apply_engine_identity

    decoded = apply_engine_identity(_enrich_from_variant(dict(decoded)))
    fuel = decoded.get("fuel") if decoded.get("fuel") in {"diesel", "gasoline"} else None
    has_engine = bool(decoded.get("displacement_l") or decoded.get("displacement_cc") or decoded.get("engine_label"))
    electric = bool(re.search(r"\b(electric|ev|bev)\b", str(decoded.get("engine_label") or ""), re.I))
    return {
        "year": decoded.get("year"),
        "make": decoded.get("make"),
        "model": decoded.get("model"),
        "trim": decoded.get("trim"),
        "body": decoded.get("body"),
        "engine_label": decoded.get("engine_label"),
        "engine_code": decoded.get("engine_code"),
        "engine_family": decoded.get("engine_family"),
        "fuel": fuel,
        "fuel_source": decoded.get("fuel_source") if fuel else None,
        "needs_fuel_confirmation": bool(has_engine and not fuel and not electric and decoded.get("model")),
        "transmission": decoded.get("transmission"),
        "variant_id": decoded.get("variant_id"),
        "source": decoded.get("source"),
        "confidence": decoded.get("confidence"),
        "note": decoded.get("note"),
        "label": vehicle_label(decoded),
        "specs": decoded.get("specs") or [],
        "displacement_l": decoded.get("displacement_l"),
        "displacement_cc": decoded.get("displacement_cc"),
        "plant_country": decoded.get("plant_country"),
        "history": decoded.get("history"),
    }


def vehicle_catalog() -> list[tuple[str, str]]:
    from app.db import fetch_all

    try:
        rows = fetch_all("SELECT DISTINCT make, model FROM vehicles ORDER BY make, model")
    except Exception:
        return []
    return [(r["make"], r["model"]) for r in rows if r.get("make") and r.get("model")]


def model_year_ranges(make: str, model: str) -> list[tuple[int, int]]:
    from app.db import fetch_all

    try:
        rows = fetch_all(
            """
            SELECT DISTINCT year_from, year_to FROM vehicles
             WHERE lower(make) = lower(%s) AND lower(model) = lower(%s)
             ORDER BY year_from
            """,
            (make, model),
        )
    except Exception:
        return []
    return [(int(r["year_from"]), int(r["year_to"])) for r in rows if r.get("year_from") and r.get("year_to")]


def vehicle_catalog_with_years() -> list[dict[str, Any]]:
    """What the understanding model is allowed to recognise: our manuals, with year ranges."""
    from app.db import fetch_all

    try:
        rows = fetch_all(
            "SELECT make, model, year_from, year_to FROM vehicles ORDER BY make, model, year_from"
        )
    except Exception:
        return []
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if not (r.get("make") and r.get("model")):
            continue
        entry = out.setdefault((r["make"], r["model"]), {"make": r["make"], "model": r["model"], "years": []})
        if r.get("year_from") and r.get("year_to"):
            span = (int(r["year_from"]), int(r["year_to"]))
            if span not in entry["years"]:
                entry["years"].append(span)
    return list(out.values())


def decoded_from_mention(mention: dict[str, Any] | None) -> dict[str, Any] | None:
    """Vehicle profile from a structured mention (make/model/year/displacement/fuel).
    Marked source "text" so the UI and the answer say it came from the user's words."""
    if not mention or not (mention.get("make") and mention.get("model")):
        return None
    engine_label = f"{mention['displacement_l']}L" if mention.get("displacement_l") else None
    return {
        "source": "text",
        "raw": None,
        "make": mention["make"],
        "model": mention["model"],
        "year": mention.get("year"),
        "engine_code": None,
        "engine_label": engine_label,
        "displacement_l": mention.get("displacement_l"),
        "fuel": mention.get("fuel"),
        "fuel_source": "user" if mention.get("fuel") else None,
        "transmission": None,
        "trim": None,
        "body": None,
        "plant_country": None,
        "confidence": "partial",
        "note": "Identified from your message, not a VIN. Add the VIN for the exact engine.",
        "specs": [],
    }


def decoded_from_text(
    message: str, catalog: list[tuple[str, str]], context: str = ""
) -> dict[str, Any] | None:
    """Offline fallback (no API key): regex read of the car from the conversation."""
    from app.intent import parse_vehicle_details, parse_vehicle_mention

    mention = parse_vehicle_mention(message, catalog) or (
        parse_vehicle_mention(context, catalog) if context else None
    )
    if not mention:
        return None
    for source in (context, message):
        if not source:
            continue
        for key, value in parse_vehicle_details(source).items():
            if value:
                mention[key] = value
    return decoded_from_mention(mention)


def resolve_vehicle(
    message: str,
    vin: str | None,
    session_vin: str | None = None,
    context: str = "",
    mention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pin the car. `mention` is the understanding model's read of the car from the text;
    when it is None (offline) the regex fallback reads the message and context."""
    from app.intent import is_follow_up

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
    if not decoded:
        # No VIN: the question itself may name the car. Never search every manual blindly.
        from app.vin import _match_variant

        decoded = decoded_from_mention(mention) if mention is not None else decoded_from_text(
            message, vehicle_catalog(), context
        )
        if decoded:
            decoded["variant_id"] = _match_variant(decoded)
    hints = infer_hints_from_decode(decoded) + infer_hints_from_text(message)
    if context and is_follow_up(message):
        hints += infer_hints_from_text(context)
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
