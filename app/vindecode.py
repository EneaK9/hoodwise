"""Layered VIN identification. Every asserted field carries its basis.

Layers, in precedence order:
1. user      the owner confirmed it for this VIN (vin_confirmations, source 'user')
2. key       the maker's own VIN key, read from a workshop manual (vin_keys), cited to a page
3. learned   other confirmed cars sharing this VIN's factory pattern (vin_confirmations)
4. vpic      the US government database, exact for US-market VINs (schema vpic)
5. provider  a commercial decoder's explicit field (evidence, never guessed from)
6. structure ISO 3779: manufacturer from the WMI table, year from position 10, check digit

Nothing here guesses. A field with no layer behind it stays empty and the app asks.
"""

from __future__ import annotations

import re
from typing import Any

from app.db import fetch_all, fetch_one

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
FIELDS = ("make", "model", "year", "body", "grade", "engine", "fuel", "displacement_l", "transmission", "drive_side", "plant", "restraint")

# ISO 3779 check digit transliteration and weights (mandatory for North American VINs only).
_TRANSLIT = {**{str(d): d for d in range(10)}, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
             "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9, "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_YEAR_CYCLE = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 2010..2039, repeats every 30 years


def valid_format(vin: str) -> bool:
    return bool(VIN_RE.fullmatch((vin or "").strip().upper()))


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[ch] * w for ch, w in zip(vin.upper(), _WEIGHTS))
    rem = total % 11
    return "X" if rem == 10 else str(rem)


def is_north_american(vin: str) -> bool:
    return vin[0] in "12345"


def year_from_code(vin: str, now: int = 2026) -> int | None:
    ch = vin[9].upper()
    if ch not in _YEAR_CYCLE:
        return None
    year = 2010 + _YEAR_CYCLE.index(ch)
    if year > now + 1:
        year -= 30
    return year


def pattern(vin: str) -> str:
    """Factory pattern: WMI, descriptor positions 4-8, year and plant. Position 9 is skipped
    because on North American VINs it is a check digit that changes with the serial."""
    v = vin.upper()
    return f"{v[:8]}_{v[9:11]}"


# ---------------------------------------------------------------- layer 6: structure

def structure(vin: str) -> dict[str, Any]:
    v = vin.upper()
    out: dict[str, Any] = {"wmi": v[:3], "region_north_america": is_north_american(v), "serial": v[11:]}
    row = None
    try:
        row = fetch_one(
            """
            SELECT w.wmi, m.name AS manufacturer, c.name AS country, vt.name AS vehicle_type,
                   (SELECT string_agg(DISTINCT mk.name, ' / ') FROM vpic.wmi_make wm JOIN vpic.make mk ON mk.id = wm.makeid WHERE wm.wmiid = w.id) AS make
              FROM vpic.wmi w
              LEFT JOIN vpic.manufacturer m ON m.id = w.manufacturerid
              LEFT JOIN vpic.country c ON c.id = w.countryid
              LEFT JOIN vpic.vehicletype vt ON vt.id = w.vehicletypeid
             WHERE w.wmi = %s
            """,
            (v[:3],),
        )
    except Exception:
        row = None
    if row:
        out.update({k: row.get(k) for k in ("manufacturer", "country", "vehicle_type", "make")})
    out["year"] = year_from_code(v)
    if out["region_north_america"]:
        out["check_digit_ok"] = check_digit(v) == v[8]
    else:
        out["check_digit_ok"] = None  # position 9 is maker-defined outside North America
    return out


# ---------------------------------------------------------------- layer 4: vPIC

_VPIC_FIELDS = {
    "Make": "make", "Model": "model", "Model Year": "year", "Body Class": "body", "Trim": "grade",
    "Engine Model": "engine", "Fuel Type - Primary": "fuel", "Displacement (L)": "displacement_l",
    "Transmission Style": "transmission", "Plant Country": "plant",
}
_VPIC_UNTRUSTED_ERRORS = {5, 6, 7, 8, 14, 400}


def vpic_decode(vin: str) -> dict[str, Any] | None:
    try:
        rows = fetch_all("SELECT variable, value FROM vpic.spvindecode(%s)", (vin.upper(),))
    except Exception:
        return None
    if not rows:
        return None
    raw = {r["variable"]: (r["value"] or "").strip() for r in rows if r.get("value")}
    codes = [int(c) for c in re.findall(r"\d+", raw.get("Error Code", "")) if c.isdigit()]
    trusted = not any(c in _VPIC_UNTRUSTED_ERRORS for c in codes)
    fields = {}
    for var, key in _VPIC_FIELDS.items():
        val = raw.get(var)
        if val and val.lower() not in {"not applicable", "n/a"}:
            fields[key] = val
    if fields.get("fuel"):
        fields["fuel"] = fields["fuel"].lower()
    return {"fields": fields, "error_codes": codes, "error_text": raw.get("Error Text", ""), "trusted": trusted}


# ---------------------------------------------------------------- layer 2: manual keys

def key_decode(vin: str) -> dict[str, list[dict[str, Any]]]:
    """Every key row whose code matches this VIN at its positions, grouped by field.
    Rows scoped to the other market are dropped; the rest are candidates."""
    v = vin.upper()
    try:
        rows = fetch_all(
            "SELECT * FROM vin_keys WHERE %s = ANY(wmis) ORDER BY position_from, field",
            (v[:3],),
        )
    except Exception:
        return {}
    na = is_north_american(v)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        scope = r.get("market_scope") or "all"
        if scope == "north_america" and not na or scope == "outside_north_america" and na:
            continue
        segment = v[r["position_from"] - 1:r["position_to"]]
        if r.get("code") is None or segment.upper() == str(r["code"]).upper():
            out.setdefault(r["field"], []).append({
                "value": r["value"], "code": r.get("code"), "market": r.get("market"),
                "attrs": r.get("attrs") or {}, "source_doc": r.get("source_doc"),
                "source_page": r.get("source_page"), "model_line": r.get("model_line"),
            })
    return out


# ---------------------------------------------------------------- layers 1 & 3: confirmations

def record_confirmation(vin: str, field: str, value: str, source: str) -> None:
    from app.db import get_conn

    v = vin.strip().upper()
    if not valid_format(v) or not value:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vin_confirmations WHERE vin = %s AND field = %s AND source = %s",
                (v, field, source),
            )
            cur.execute(
                "INSERT INTO vin_confirmations (vin, pattern, field, value, source) VALUES (%s, %s, %s, %s, %s)",
                (v, pattern(v), field, str(value), source),
            )
        conn.commit()


def confirmations(vin: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(this VIN's own confirmations, learned from other VINs with the same pattern)."""
    v = vin.upper()
    try:
        rows = fetch_all(
            "SELECT vin, field, value, source FROM vin_confirmations WHERE pattern = %s ORDER BY created_at",
            (pattern(v),),
        )
    except Exception:
        return {}, {}
    own: dict[str, dict[str, Any]] = {}
    votes: dict[str, dict[str, set]] = {}
    for r in rows:
        if r["vin"] == v:
            own[r["field"]] = {"value": r["value"], "source": r["source"]}
        else:
            votes.setdefault(r["field"], {}).setdefault(r["value"], set()).add(r["vin"])
    learned: dict[str, dict[str, Any]] = {}
    for field, by_value in votes.items():
        if len(by_value) != 1:
            continue  # other cars disagree: not evidence
        value, vins = next(iter(by_value.items()))
        if len(vins) >= 2:
            learned[field] = {"value": value, "count": len(vins)}
    return own, learned


# ---------------------------------------------------------------- merge

def identify(vin: str, provider: dict[str, Any] | None = None) -> dict[str, Any]:
    v = (vin or "").strip().upper()
    if not valid_format(v):
        return {"valid": False, "fields": {}, "candidates": {}}
    struct = structure(v)
    vpic = vpic_decode(v)
    keys = key_decode(v)
    own, learned = confirmations(v)
    provider = provider or {}

    fields: dict[str, dict[str, Any]] = {}
    candidates: dict[str, list[dict[str, Any]]] = {}

    def put(field: str, value: Any, source: str, basis: str) -> None:
        if value in (None, "", []) or field in fields:
            return
        fields[field] = {"value": value, "source": source, "basis": basis}

    # 1. the owner
    for field, c in own.items():
        put(field, c["value"], c["source"], "confirmed for this VIN" if c["source"] == "user" else f"confirmed by {c['source']}")

    # 2. the maker's key from a workshop manual. Position 1-3 rows describe the WMI class,
    #    not a make name, and the check digit / serial rows carry no identity: only their
    #    decomposed attributes (transmission, drive side, fuel...) are asserted.
    direct = {"model", "grade", "body", "restraint", "engine", "year", "plant", "drive_transmission"}
    for field, cands in keys.items():
        distinct = {c["value"] for c in cands}
        if len(distinct) != 1:
            candidates[field] = cands
            continue
        c = cands[0]
        page = f" p.{c['source_page']}" if c.get("source_page") else ""
        basis = f"VIN key in {c.get('source_doc') or 'the workshop manual'}{page}, code {c.get('code')}"
        if field in direct:
            value: Any = c["value"]
            if field == "year" and str(value).isdigit():
                value = int(value)
            put(field, value, "key", basis)
        for attr, val in (c.get("attrs") or {}).items():
            put(attr, val, "key", basis)

    # 3. learned from other confirmed cars with the same factory pattern
    for field, l in learned.items():
        put(field, l["value"], "learned", f"{l['count']} confirmed cars share this factory pattern")

    # 4. US government database, only when it reports a clean decode
    if vpic and vpic["trusted"]:
        for field, val in vpic["fields"].items():
            put(field, val, "vpic", "NHTSA vPIC database (US-market VIN)")

    # 5. commercial decoder explicit fields
    for field in ("make", "model", "year", "body", "transmission", "trim"):
        val = provider.get(field)
        if val and str(val).lower() not in {"any", "none"}:
            put("grade" if field == "trim" else field, val, "provider", f"{provider.get('source') or 'decoder'} field")
    if provider.get("displacement_l"):
        put("displacement_l", str(provider["displacement_l"]), "provider", f"{provider.get('source') or 'decoder'} displacement")

    # 6. structure
    put("make", struct.get("make") or struct.get("manufacturer"), "structure", f"WMI {struct['wmi']} in the manufacturer register")
    put("year", struct.get("year"), "structure", "VIN position 10 year code (ISO 3779)")
    if struct.get("country"):
        put("built_in", struct["country"], "structure", f"WMI {struct['wmi']} country")

    return {
        "valid": True,
        "vin": v,
        "fields": fields,
        "candidates": candidates,
        "structure": struct,
        "vpic": {"trusted": vpic["trusted"], "error_codes": vpic["error_codes"]} if vpic else None,
        "unknown": [f for f in ("model", "year", "engine", "fuel", "transmission") if f not in fields],
    }


def basis_lines(identity: dict[str, Any] | None) -> list[str]:
    if not identity or not identity.get("fields"):
        return []
    order = ("model", "year", "grade", "body", "engine", "displacement_l", "fuel", "transmission", "drive_side", "plant", "make")
    out = []
    for f in order:
        e = identity["fields"].get(f)
        if e:
            out.append(f"{f.replace('_', ' ')}: {e['value']} ({e['basis']})")
    return out
