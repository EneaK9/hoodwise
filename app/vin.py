"""VIN decode: NHTSA vPIC, then optional global APIs, then Civic pattern / WMI."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx

from app.config import settings
from app.db import fetch_all, fetch_one, get_conn

VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.IGNORECASE)
WMI = {
    "19X": ("Honda", "United States"),
    "2HG": ("Honda", "Canada"),
    "1HG": ("Honda", "United States"),
    "JHM": ("Honda", "Japan"),
    "SHH": ("Honda", "United Kingdom"),
    "93H": ("Honda", "Brazil"),
    "KMH": ("Hyundai", "South Korea"),
    "KM8": ("Hyundai", "South Korea"),
    "KNA": ("Kia", "South Korea"),
    "WVW": ("Volkswagen", "Germany"),
    "WBA": ("BMW", "Germany"),
}

# vPIC error 1 (bad check digit) is common and still often usable.
# 5/6/7/8/14/400 mean the model/year guess is not trustworthy.
SERIOUS_VPIC_ERRORS = {5, 6, 7, 8, 14, 400}

# ISO 3779 year char, 2010–2039 cycle (I/O/Q/U/Z/0 unused).
ISO_YEAR_2010 = {
    "A": 2010, "B": 2011, "C": 2012, "D": 2013, "E": 2014, "F": 2015,
    "G": 2016, "H": 2017, "J": 2018, "K": 2019, "L": 2020, "M": 2021,
    "N": 2022, "P": 2023, "R": 2024, "S": 2025, "T": 2026, "V": 2027,
    "W": 2028, "X": 2029, "Y": 2030,
    "1": 2031, "2": 2032, "3": 2033, "4": 2034, "5": 2035,
    "6": 2036, "7": 2037, "8": 2038, "9": 2039,
}

# Positions 4-8 for 10th-gen Civic (US/UK/JP common codes). Partial, hand-built.
CIVIC_VDS = {
    "FC1F5": {"engine_label": "1.5T", "body": "Sedan", "transmission": "CVT"},
    "FC1F7": {"engine_label": "1.5T", "body": "Sedan", "transmission": "CVT"},
    "FC1F9": {"engine_label": "1.5T", "body": "Sedan", "transmission": "MT"},
    "FC2F5": {"engine_label": "2.0L", "body": "Sedan", "transmission": "CVT"},
    "FC2F6": {"engine_label": "2.0L", "body": "Sedan", "transmission": "CVT"},
    "FC2F8": {"engine_label": "2.0L", "body": "Sedan", "transmission": "MT"},
    "FK7H4": {"engine_label": "1.5T", "body": "Hatchback", "transmission": "CVT"},
    "FK7H5": {"engine_label": "1.5T", "body": "Hatchback", "transmission": "CVT"},
    "FK7H9": {"engine_label": "1.5T", "body": "Hatchback", "transmission": "MT"},
    "FK8H1": {"engine_label": "Type-R 2.0T", "body": "Hatchback", "transmission": "MT"},
    "FC1H5": {"engine_label": "1.5T Si", "body": "Sedan", "transmission": "MT"},
    "FK7G1": {"engine_label": "1.5T Si", "body": "Hatchback", "transmission": "MT"},
}

YEAR_CODES = {k: v for k, v in ISO_YEAR_2010.items() if 2016 <= v <= 2021}


def iso_model_year(vin: str, now: int | None = None) -> int | None:
    """VIN position 10 repeats every 30 years. Prefer the recent, not-future cycle."""
    if len(vin) < 10:
        return None
    year = ISO_YEAR_2010.get(vin[9].upper())
    if year is None:
        return None
    current = now or __import__("datetime").date.today().year
    if year > current + 1:
        year -= 30
    if current - year >= 30:
        newer = year + 30
        if newer <= current + 1:
            year = newer
    return year


def _vpic_error_codes(row: dict[str, Any]) -> list[int]:
    raw = str(row.get("ErrorCode") or "0")
    codes: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            codes.append(int(part))
    return codes


def vpic_model_is_trusted(row: dict[str, Any]) -> bool:
    codes = _vpic_error_codes(row)
    return not any(code in SERIOUS_VPIC_ERRORS for code in codes)


def find_vins(text: str) -> list[str]:
    return [m.group(1).upper() for m in VIN_RE.finditer(text or "")]


def _match_variant(decoded: dict[str, Any]) -> str | None:
    make = (decoded.get("make") or "").lower()
    model = (decoded.get("model") or "").lower()
    year = decoded.get("year")
    engine = (decoded.get("engine_code") or "").upper()
    engine_label = decoded.get("engine_label") or ""
    trans = (decoded.get("transmission") or "").upper()
    body = (decoded.get("body") or "").lower()
    trim = (decoded.get("trim") or "").lower()

    trans_norm = "CVT" if "CVT" in trans or "CONTINUOUS" in trans else "MT" if trans else None
    rows = fetch_all(
        """
        SELECT vv.id, vv.engine_code, vv.engine_label, vv.transmission, vv.trim, vv.body,
               vv.year_from, vv.year_to, v.make, v.model
          FROM vehicle_variants vv
          JOIN vehicles v ON v.id = vv.vehicle_id
        """
    )
    scored: list[tuple[int, str]] = []
    for row in rows:
        rmake = (row["make"] or "").lower()
        rmodel = (row["model"] or "").lower()
        if make and rmake != make:
            continue
        if model and rmodel != model and model not in rmodel and rmodel not in model:
            continue
        score = 0
        if make and rmake == make:
            score += 5
        if model and (rmodel == model or model in rmodel or rmodel in model):
            score += 5
        if year and row["year_from"] and row["year_to"] and row["year_from"] <= year <= row["year_to"]:
            score += 4
        if rmake == "honda":
            if engine and (row["engine_code"] or "").upper() == engine:
                score += 4
            if engine_label and engine_label.lower() in (row["engine_label"] or "").lower():
                score += 3
            if trans_norm and row["transmission"] == trans_norm:
                score += 2
            if body and body[:5] in (row["body"] or "").lower():
                score += 1
            if trim and row["trim"] and trim in row["trim"].lower():
                score += 2
        else:
            blob = f"{engine_label} {trim}".lower()
            label = (row["engine_label"] or "").lower()
            if label in blob and label not in {"owner manual"}:
                score += 2
        if score:
            scored.append((score, str(row["id"])))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None


def decode_is_complete(decoded: dict[str, Any] | None) -> bool:
    if not decoded:
        return False
    if not (decoded.get("make") and decoded.get("model")):
        return False
    return decoded.get("confidence") == "full"


def has_global_vin_api() -> bool:
    if settings.vincario_api_key and settings.vincario_secret_key:
        return True
    if settings.carsxe_api_key or settings.api_ninjas_key:
        return True
    return False


def _http_get_json(url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any | None:
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url, params=params, headers=headers)
            return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def _vpic(vin: str) -> dict[str, Any] | None:
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url, params={"format": "json"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return None
    results = data.get("Results") or []
    if not results:
        return None
    row = results[0]
    return _from_vpic_row(row, vin)


def _from_vpic_row(row: dict[str, Any], vin: str) -> dict[str, Any] | None:
    make = (row.get("Make") or "").strip()
    model = (row.get("Model") or "").strip()
    if not make:
        return None
    trusted = vpic_model_is_trusted(row)
    year = None
    if trusted:
        try:
            year = int(row.get("ModelYear") or 0) or None
        except ValueError:
            year = None
    if not year:
        year = iso_model_year(vin)
    if not trusted:
        model = None
        trim = None
        engine = None
        trans = None
        body = None
        note = (
            "NHTSA vPIC (US VIN API) could not confirm this vehicle. "
            "Showing make and year-code only — model was not trusted."
        )
        confidence = "partial"
    else:
        trim = (row.get("Trim") or "").strip() or None
        engine = (row.get("EngineModel") or "").strip() or None
        trans = (row.get("TransmissionStyle") or "").strip() or None
        body = (row.get("BodyClass") or "").strip() or None
        note = None
        confidence = "full" if model and year else "partial"
    return {
        "source": "vpic",
        "raw": row,
        "make": make,
        "model": model,
        "year": year,
        "engine_code": engine,
        "engine_label": None,
        "displacement_l": row.get("DisplacementL") if trusted else None,
        "transmission": trans,
        "trim": trim,
        "body": body,
        "plant_country": (row.get("PlantCountry") or "").strip() or None,
        "confidence": confidence,
        "note": note,
        "vpic_errors": _vpic_error_codes(row),
    }


def _pattern(vin: str) -> dict[str, Any] | None:
    vds = vin[3:8]
    wmi = vin[:3]
    year = iso_model_year(vin)
    spec = CIVIC_VDS.get(vds)
    if not spec:
        return None
    make, country = WMI.get(wmi, ("Honda", "unknown"))
    return {
        "source": "pattern",
        "raw": {"vds": vds, "wmi": wmi},
        "make": make,
        "model": "Civic",
        "year": year,
        "engine_code": None,
        "engine_label": spec["engine_label"],
        "displacement_l": None,
        "transmission": spec["transmission"],
        "trim": "Type-R" if "Type-R" in spec["engine_label"] else None,
        "body": spec["body"],
        "plant_country": country,
        "confidence": "full" if year else "partial",
    }


def _wmi_only(vin: str) -> dict[str, Any]:
    wmi = vin[:3]
    make, country = WMI.get(wmi, ("Unknown", "Unknown"))
    year = iso_model_year(vin)
    return {
        "source": "wmi",
        "raw": {"wmi": wmi},
        "make": make,
        "model": None,
        "year": year,
        "engine_code": None,
        "engine_label": None,
        "displacement_l": None,
        "transmission": None,
        "trim": None,
        "body": None,
        "plant_country": country,
        "confidence": "wmi_only",
        "note": (
            "NHTSA vPIC has no confirmed record for this VIN. "
            "Only the manufacturer/country code was read. "
            "A Vincario or CarsXE key is needed to look up non-US vehicles."
        ),
    }


SPEC_SKIP_LABELS = {
    "vin",
    "vehicle id",
    "make logo",
    "check digit",
    "sequential number",
    "wheel size array",
    "wheelbase array (mm)",
    "wheel rims size array",
}


def _format_spec_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return ", ".join(str(x) for x in value if x not in (None, ""))
    text = str(value).strip()
    return text or None


def _vincario_pairs(payload: dict[str, Any]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in payload.get("decode") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip().lower()
        value = _format_spec_value(item.get("value"))
        if label and value:
            pairs[label] = value
    return pairs


def vincario_specs(payload: dict[str, Any]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for item in payload.get("decode") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label or label.lower() in SPEC_SKIP_LABELS:
            continue
        value = _format_spec_value(item.get("value"))
        if value:
            specs.append({"label": label, "value": value})
    return specs


def from_vincario(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("error"):
        return None
    pairs = _vincario_pairs(payload)
    make = pairs.get("make") or pairs.get("manufacturer")
    model = pairs.get("model")
    if not (make and model):
        return None
    year = None
    raw_year = pairs.get("model year") or pairs.get("year")
    if raw_year:
        try:
            year = int(str(raw_year)[:4])
        except ValueError:
            year = None
    displacement_l = pairs.get("displacement (l)") or pairs.get("displacement")
    ccm = pairs.get("engine displacement (ccm)")
    if not displacement_l and ccm:
        try:
            displacement_l = f"{round(int(ccm) / 1000, 1):.1f}"
        except ValueError:
            displacement_l = None
    engine_label = pairs.get("engine type") or pairs.get("engine")
    if not engine_label and displacement_l:
        engine_label = f"{displacement_l}L"
    return {
        "source": "vincario",
        "raw": None,
        "make": make,
        "model": model,
        "year": year,
        "engine_code": pairs.get("engine code") or pairs.get("engine"),
        "engine_label": engine_label,
        "displacement_l": displacement_l,
        "transmission": pairs.get("transmission"),
        "trim": pairs.get("trim") or pairs.get("series"),
        "body": pairs.get("body") or pairs.get("product type"),
        "plant_country": pairs.get("plant country"),
        "confidence": "full",
        "note": "Looked up via Vincario.",
        "specs": vincario_specs(payload),
    }


def from_carsxe(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("success") is False:
        return None
    make = (payload.get("make") or payload.get("Make") or "").strip()
    model = (payload.get("model") or payload.get("Model") or "").strip()
    if not (make and model):
        return None
    year = payload.get("year") or payload.get("Year")
    try:
        year_i = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_i = None
    return {
        "source": "carsxe",
        "raw": None,
        "make": make,
        "model": model,
        "year": year_i,
        "engine_code": payload.get("engine") or payload.get("engine_model"),
        "engine_label": payload.get("engine") or payload.get("engine_model"),
        "displacement_l": payload.get("displacement") or payload.get("displacement_l"),
        "transmission": payload.get("transmission") or payload.get("transmission_style"),
        "trim": payload.get("trim") or payload.get("Trim"),
        "body": payload.get("body") or payload.get("body_class") or payload.get("style"),
        "plant_country": payload.get("plant_country") or payload.get("made_in"),
        "confidence": "full",
        "note": "Looked up via CarsXE.",
    }


def from_api_ninjas(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("error"):
        return None
    make = (payload.get("manufacturer") or payload.get("make") or "").strip()
    model = (payload.get("model") or "").strip()
    if not (make and model):
        return None
    year = payload.get("year")
    try:
        year_i = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_i = None
    return {
        "source": "api_ninjas",
        "raw": None,
        "make": make,
        "model": model,
        "year": year_i,
        "engine_code": None,
        "engine_label": None,
        "displacement_l": None,
        "transmission": payload.get("transmission"),
        "trim": None,
        "body": payload.get("class") or payload.get("body"),
        "plant_country": payload.get("country"),
        "confidence": "full",
        "note": "Looked up via API Ninjas.",
    }


VINCARIO_BASE = "https://api.vincario.com/3.2"


def vincario_control_sum(*, endpoint_id: str, key: str, secret: str, vin: str | None = None) -> str:
    """First 10 hex chars of SHA1(VIN|ID|key|secret) or SHA1(ID|key|secret)."""
    material = f"{vin}|{endpoint_id}|{key}|{secret}" if vin else f"{endpoint_id}|{key}|{secret}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:10]


def _vincario_get(endpoint_id: str, path: str, vin: str | None = None) -> dict[str, Any] | None:
    key = settings.vincario_api_key.strip()
    secret = settings.vincario_secret_key.strip()
    if not (key and secret):
        return None
    control = vincario_control_sum(endpoint_id=endpoint_id, key=key, secret=secret, vin=vin)
    payload = _http_get_json(f"{VINCARIO_BASE}/{key}/{control}/{path}")
    return payload if isinstance(payload, dict) else None


STOLEN_REGIONS = {
    "cz": "Czechia",
    "hu": "Hungary",
    "ro": "Romania",
    "si": "Slovenia",
    "sk": "Slovakia",
    "lt": "Lithuania",
    "vincario": "Vincario database",
}


def from_vincario_stolen(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in payload.get("stolen") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().lower()
        status = str(item.get("status") or "").replace("-", " ").strip()
        if not (code and status):
            continue
        rows.append({"region": STOLEN_REGIONS.get(code, code.upper()), "status": status})
    return rows


def from_vincario_market(payload: dict[str, Any]) -> dict[str, Any] | None:
    price = (payload.get("market_price") or {}).get("europe") or {}
    odo = (payload.get("market_odometer") or {}).get("europe") or {}
    if not price:
        return None
    records = payload.get("records") or []
    markets = sorted(
        {
            str(row.get("market"))
            for row in records
            if isinstance(row, dict) and row.get("market")
        }
    )
    return {
        "region": "Europe",
        "currency": price.get("price_currency"),
        "median": price.get("price_median"),
        "low": price.get("price_below"),
        "high": price.get("price_above"),
        "odometer_median_km": odo.get("odometer_median"),
        "sample_size": price.get("price_count"),
        "markets": markets,
        "period": payload.get("period"),
    }


def enrich_vincario_history(decoded: dict[str, Any], vin: str) -> dict[str, Any]:
    """Stolen + EU market value. Not Albanian registration or owner history."""
    vin = vin.upper()
    stolen_rows: list[dict[str, str]] = []
    stolen_payload = _vincario_get("stolen-check", f"stolen-check/{vin}.json", vin=vin)
    if stolen_payload and not stolen_payload.get("error"):
        stolen_rows = from_vincario_stolen(stolen_payload)
    market = None
    market_payload = _vincario_get(
        "vehicle-market-value", f"vehicle-market-value/{vin}.json", vin=vin
    )
    if market_payload and not market_payload.get("error"):
        market = from_vincario_market(market_payload)
    decoded["history"] = {
        "note": (
            "A VIN is factory identity. It does not store the current country, "
            "owners, accidents, or Albanian DPSHTRR registration."
        ),
        "stolen": stolen_rows,
        "stolen_note": (
            "Stolen lists checked: Czechia, Hungary, Romania, Slovenia, and Vincario. "
            "Albania is not in this check."
        ),
        "market": market,
        "market_note": (
            "Recent European listing prices (not the Albanian market). "
            "OEM service history is not enabled on this Vincario account."
        ),
    }
    return decoded


def _vincario(vin: str) -> dict[str, Any] | None:
    vin = vin.upper()
    payload = _vincario_get("decode", f"decode/{vin}.json", vin=vin)
    if not payload:
        return None
    if payload.get("error"):
        return {"_error": f"Vincario: {payload.get('message') or 'error'}"}
    return from_vincario(payload)


def _carsxe(vin: str) -> dict[str, Any] | None:
    key = settings.carsxe_api_key.strip()
    if not key:
        return None
    # /specs is US-centric; international decoder covers EU/Korea VINs.
    paths = ("v1/international-vin-decoder", "international-vin-decoder", "specs")
    last_error = None
    for path in paths:
        payload = _http_get_json(f"https://api.carsxe.com/{path}", params={"key": key, "vin": vin})
        if not isinstance(payload, dict):
            continue
        if payload.get("success") is False or payload.get("error"):
            last_error = str(payload.get("message") or payload.get("error") or "CarsXE error")
            continue
        parsed = from_carsxe(payload)
        if parsed:
            return parsed
    if last_error:
        return {"_error": f"CarsXE: {last_error}"}
    return None


def _api_ninjas(vin: str) -> dict[str, Any] | None:
    key = settings.api_ninjas_key.strip()
    if not key:
        return None
    payload = _http_get_json(
        "https://api.api-ninjas.com/v1/vinlookup",
        params={"vin": vin},
        headers={"X-Api-Key": key},
    )
    if not isinstance(payload, dict):
        return None
    return from_api_ninjas(payload)


def pick_decode(vin: str) -> dict[str, Any]:
    """Call VIN APIs in order. Never invent a model from WMI/year alone."""
    vpic = _vpic(vin)
    if decode_is_complete(vpic):
        return vpic  # type: ignore[return-value]
    failures: list[str] = []
    for provider in (_vincario, _carsxe, _api_ninjas):
        got = provider(vin)
        if isinstance(got, dict) and got.get("_error"):
            failures.append(str(got["_error"]))
            continue
        if decode_is_complete(got):
            return got  # type: ignore[return-value]
    fallback = _pattern(vin) or vpic or _wmi_only(vin)
    if failures:
        extra = " ".join(failures)
        fallback["note"] = f"{fallback.get('note') or ''} {extra}".strip()
    return fallback


def decode_vin(vin: str) -> dict[str, Any]:
    vin = vin.strip().upper()
    if not VIN_RE.fullmatch(vin):
        raise ValueError("Invalid VIN")

    cached = fetch_one(
        """
        SELECT v.vin, v.raw_response, v.variant_id, v.source, v.confidence, v.decoded_at,
               vv.engine_label, vv.transmission, vv.trim, vv.body
          FROM vin_decodes v
          LEFT JOIN vehicle_variants vv ON vv.id = v.variant_id
         WHERE v.vin = %s
        """,
        (vin,),
    )
    if cached:
        cached_body = cached["raw_response"] or {}
        # Drop poisoned US-decoder guesses (e.g. 1986 Stellar) so we re-decode.
        cached_errors = cached_body.get("vpic_errors") or []
        stale_guess = (cached_body.get("model") or "").lower() == "stellar"
        incomplete = not cached_body.get("model")
        missing_specs = cached.get("source") == "vincario" and not cached_body.get("specs")
        retry_global = (incomplete or missing_specs) and has_global_vin_api()
        if (
            stale_guess
            or retry_global
            or any(int(c) in SERIOUS_VPIC_ERRORS for c in cached_errors if str(c).isdigit() or isinstance(c, int))
        ):
            cached = None
        else:
            body = {
                **cached_body,
                "vin": vin,
                "variant_id": str(cached["variant_id"]) if cached["variant_id"] else None,
                "cached": True,
                "source": cached["source"],
                "confidence": cached["confidence"],
            }
            if not body.get("variant_id"):
                body["variant_id"] = _match_variant(body)
            if body.get("source") == "vincario" and not body.get("history") and has_global_vin_api():
                body = enrich_vincario_history(body, vin)
                body["cached"] = False
                _store_decode(body)
            elif body.get("variant_id") and not cached_body.get("variant_id"):
                _store_decode(body)
            return body

    decoded = pick_decode(vin)
    variant_id = _match_variant(decoded)
    decoded["variant_id"] = variant_id
    decoded["vin"] = vin
    decoded["cached"] = False
    if decoded.get("source") == "vincario":
        decoded = enrich_vincario_history(decoded, vin)

    _store_decode(decoded)
    return decoded


def _store_decode(decoded: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vin_decodes (vin, raw_response, variant_id, source, confidence)
                VALUES (%s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (vin) DO UPDATE
                  SET raw_response = EXCLUDED.raw_response,
                      variant_id = EXCLUDED.variant_id,
                      source = EXCLUDED.source,
                      confidence = EXCLUDED.confidence,
                      decoded_at = now()
                """,
                (
                    decoded["vin"],
                    json.dumps({k: v for k, v in decoded.items() if k != "raw"}),
                    decoded.get("variant_id"),
                    decoded["source"],
                    decoded["confidence"],
                ),
            )
        conn.commit()
