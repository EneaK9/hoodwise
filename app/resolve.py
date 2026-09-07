"""Resolve the VIN, the session, and the understanding model's read into one vehicle profile."""

from __future__ import annotations

from typing import Any

from app.vin import decode_vin, find_vins


def clean_part_name(part_name: str, raw_context: str = "") -> str:
    return " ".join((part_name or "").split()).strip(" -:|")[-80:] or "spec"


def _enrich_from_variant(decoded: dict[str, Any]) -> dict[str, Any]:
    """Only the vehicle link. Variant rows carry catalog labels, not engine facts."""
    return decoded


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
        str(decoded.get("fuel") or ""),
    ]
    seen: set[str] = set()
    kept: list[str] = []
    for bit in bits:
        low = bit.lower()
        if not bit or low in seen:
            continue
        seen.add(low)
        kept.append(bit)
    label = " ".join(kept).strip()
    if label and not decoded.get("model"):
        label = f"{label} (model unconfirmed)"
    return label or None


def public_vehicle(decoded: dict[str, Any] | None) -> dict[str, Any] | None:
    if not decoded:
        return None
    from app.vin import apply_engine_identity

    decoded = apply_engine_identity(dict(decoded))
    fuel = decoded.get("fuel") or None
    return {
        "year": decoded.get("year"),
        "make": decoded.get("make"),
        "model": decoded.get("model"),
        "trim": decoded.get("trim"),
        "body": decoded.get("body"),
        "engine_label": decoded.get("engine_label"),
        "engine_code": decoded.get("engine_code"),
        "fuel": fuel,
        "fuel_raw": decoded.get("fuel_raw"),
        "fuel_source": decoded.get("fuel_source") if fuel else None,
        "emissions_hint": decoded.get("emissions_hint"),
        "needs_fuel_confirmation": bool(decoded.get("model") and not fuel),
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


def vehicle_catalog_with_years() -> list[dict[str, Any]]:
    """Every manual we hold, one entry per edition: id, make, model, year range, and the
    edition label the catalog gave it (owner manual, hybrid, PHEV, electric, service manual
    engines...). The understanding model picks the entry that matches the user's car."""
    from app.db import fetch_all

    try:
        rows = fetch_all(
            """
            SELECT v.id, v.make, v.model, v.year_from, v.year_to,
                   string_agg(DISTINCT vv.engine_label, ' / ' ORDER BY vv.engine_label) AS editions,
                   count(DISTINCT d.id) AS documents
              FROM vehicles v
              LEFT JOIN vehicle_variants vv ON vv.vehicle_id = v.id
              LEFT JOIN documents d ON d.vehicle_id = v.id
             GROUP BY v.id, v.make, v.model, v.year_from, v.year_to
             ORDER BY v.make, v.model, v.year_from, editions
            """
        )
    except Exception:
        return []
    return [
        {
            "id": str(r["id"]),
            "make": r["make"],
            "model": r["model"],
            "year_from": r.get("year_from"),
            "year_to": r.get("year_to"),
            "edition": r.get("editions") or "",
            "documents": int(r.get("documents") or 0),
        }
        for r in rows
        if r.get("make") and r.get("model")
    ]


def decoded_from_mention(mention: dict[str, Any] | None) -> dict[str, Any] | None:
    """Vehicle profile from the understanding model's read of the user's words (no VIN)."""
    if not mention or not (mention.get("make") and mention.get("model")):
        return None
    engine_label = mention.get("engine") or (f"{mention['displacement_l']}L" if mention.get("displacement_l") else None)
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
        "manual_id": mention.get("manual_id"),
        "transmission": None,
        "trim": None,
        "body": None,
        "plant_country": None,
        "confidence": "partial",
        "note": "Identified from your message, not a VIN. Add the VIN for the exact engine.",
        "specs": [],
    }


def apply_model_read(decoded: dict[str, Any] | None, mention: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fill engine and fuel on a VIN decode from the understanding model's reading of its data.
    The owner's confirmation always wins; the model fills only what is empty."""
    if not decoded or not mention:
        return decoded
    if not decoded.get("fuel") and mention.get("fuel"):
        decoded["fuel"] = mention["fuel"]
        decoded["fuel_source"] = f"model: {mention.get('fuel_basis') or 'read from the VIN data'}"
    if mention.get("engine") and (not decoded.get("engine_label") or len(str(decoded.get("engine_label"))) <= 5):
        decoded["engine_label"] = mention["engine"]
    if not decoded.get("displacement_l") and mention.get("displacement_l"):
        decoded["displacement_l"] = mention["displacement_l"]
    if mention.get("manual_id"):
        decoded["manual_id"] = mention["manual_id"]
    return decoded


def resolve_vehicle(
    message: str,
    vin: str | None,
    session_vin: str | None = None,
    mention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pin the car. VIN first; otherwise the understanding model's read of the user's words."""
    found = find_vins(message)
    chosen = (vin or (found[0] if found else None) or session_vin or "").strip().upper() or None
    decoded = None
    if chosen:
        try:
            decoded = decode_vin(chosen)
        except ValueError:
            decoded = None
            chosen = None
    if decoded:
        decoded = apply_model_read(decoded, mention)
    else:
        from app.vin import _match_variant

        decoded = decoded_from_mention(mention)
        if decoded:
            decoded["variant_id"] = _match_variant(decoded)
    return {
        "vin": chosen,
        "decoded": decoded,
        "variant_id": (decoded or {}).get("variant_id"),
        "vehicle_id": _vehicle_id_for_decode(decoded),
        "label": vehicle_label(decoded),
    }


def _vehicle_id_for_decode(decoded: dict[str, Any] | None) -> str | None:
    if not decoded:
        return None
    from app.db import fetch_one

    # The understanding model chose the manual edition (petrol vs hybrid vs PHEV...).
    manual_id = decoded.get("manual_id")
    if manual_id:
        row = fetch_one(
            "SELECT id FROM vehicles WHERE id::text = %s AND lower(make) = lower(%s)",
            (str(manual_id), str(decoded.get("make") or "")),
        )
        if row:
            return str(row["id"])
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
             WHERE lower(make) = lower(%s) AND lower(model) = lower(%s)
               AND year_from <= %s AND year_to >= %s
             ORDER BY year_from DESC LIMIT 1
            """,
            (make, model, year, year),
        )
    else:
        row = fetch_one(
            "SELECT id FROM vehicles WHERE lower(make) = lower(%s) AND lower(model) = lower(%s) ORDER BY year_from DESC LIMIT 1",
            (make, model),
        )
    return str(row["id"]) if row else None
