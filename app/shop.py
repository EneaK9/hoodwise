"""Exact-spec shopping searches. We do not scrape or pick a listing."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

SHOP_INTENT = re.compile(
    r"\b(bulb|lamp|light|headlight|fog|indicator|tire|tyre|oil|filter|wiper|"
    r"fluid|coolant|battery|fuse|washer)\b",
    re.IGNORECASE,
)
SKIP_INTENT = re.compile(
    r"\b(torque|tighten|nm|bleed|procedure|how tight)\b",
    re.IGNORECASE,
)
BULB_RE = re.compile(
    r"\b(H(?:1|3|4|7|8|9|11|13|15|16|27)|HB[34]|HIR2|900[56]|P21W|PY21W|"
    r"W5W|W16W|T10|D[1-4]S|H8L)\b",
    re.IGNORECASE,
)
TIRE_RE = re.compile(r"\b\d{3}/\d{2}R\d{2}\b", re.IGNORECASE)
VISCOSITY_RE = re.compile(r"\b(?:0W|5W|10W|15W|20W)[-/](?:20|30|40|50)\b", re.IGNORECASE)
OIL_SPEC_RE = re.compile(r"\b(?:API\s+S[LM]|ILSAC\s+GF-\d|ACEA\s+A[35])\b", re.IGNORECASE)
ATF_RE = re.compile(r"\b(?:ATF\s+SP-?IV|SP-?IV|PSF-4)\b", re.IGNORECASE)
SAFE_QUERY = re.compile(r"[^A-Za-z0-9./\-\s]+")

SITES = (
    ("eBay", "https://www.ebay.com/sch/i.html?_nkw={q}"),
    ("Amazon", "https://www.amazon.de/s?k={q}"),
    ("Autodoc", "https://www.autodoc.al/search?keyword={q}"),
)


def _blob(question: str, retrieved: dict[str, Any], answer: str) -> str:
    parts = [question, answer]
    for spec in retrieved.get("specs") or []:
        parts.append(" ".join(str(spec.get(k) or "") for k in ("value_raw", "part_name", "raw_context")))
    for chunk in retrieved.get("chunks") or []:
        parts.append(chunk.get("content") or "")
    return "\n".join(parts)


def _kind(question: str) -> str:
    q = question.lower()
    if re.search(r"\b(tire|tyre)\b", q):
        return "tire"
    if re.search(r"\b(oil|lubricant)\b", q):
        return "engine oil"
    if re.search(r"\b(coolant|antifreeze)\b", q):
        return "coolant"
    if re.search(r"\b(atf|transmission)\b", q):
        return "ATF"
    if re.search(r"\b(wiper)\b", q):
        return "wiper blade"
    if re.search(r"\b(filter)\b", q):
        return "filter"
    if re.search(r"\b(bulb|lamp|light|fog|headlight)\b", q):
        return "bulb"
    return ""


NO_SPEC = re.compile(
    r"does not specify|no (?:bulb )?type|not specify a bulb|cannot quote|do not have",
    re.IGNORECASE,
)


def extract_spec(question: str, retrieved: dict[str, Any], answer: str = "") -> str | None:
    if answer and NO_SPEC.search(answer):
        return None
    text = _blob(question, retrieved, answer)
    kind = _kind(question)
    if kind == "tire":
        hit = TIRE_RE.search(text)
        return hit.group(0).upper() if hit else None
    if kind in {"engine oil", "coolant"}:
        vis = VISCOSITY_RE.search(text)
        spec = OIL_SPEC_RE.search(text)
        bits = [p.group(0) for p in (vis, spec) if p]
        return " ".join(bits) if bits else None
    if kind == "ATF":
        hit = ATF_RE.search(text)
        return hit.group(0).upper() if hit else None
    bulb = BULB_RE.search(text)
    if bulb:
        return bulb.group(0).upper()
    tire = TIRE_RE.search(text)
    if tire:
        return tire.group(0).upper()
    vis = VISCOSITY_RE.search(text)
    if vis:
        return vis.group(0)
    return None


def _vehicle_bits(vehicle: dict[str, Any] | None) -> list[str]:
    if not vehicle:
        return []
    out: list[str] = []
    for key in ("year", "make", "model"):
        value = vehicle.get(key)
        if value:
            out.append(str(value))
    return out


def search_query(spec: str, question: str, vehicle: dict[str, Any] | None) -> str:
    parts = _vehicle_bits(vehicle)
    if spec:
        parts.append(spec)
    kind = _kind(question)
    if kind and kind.lower() not in spec.lower():
        parts.append(kind)
    raw = SAFE_QUERY.sub(" ", " ".join(parts))
    return re.sub(r"\s+", " ", raw).strip()[:90]


def shop_links(
    question: str,
    retrieved: dict[str, Any],
    vehicle: dict[str, Any] | None = None,
    answer: str = "",
) -> dict[str, Any] | None:
    if SKIP_INTENT.search(question) and not SHOP_INTENT.search(question):
        return None
    if not SHOP_INTENT.search(question):
        return None
    spec = extract_spec(question, retrieved, answer)
    label = spec or _kind(question)
    query = search_query(label, question, vehicle)
    if not query:
        return None
    extra = []
    for word in ("fog", "headlight", "cabin", "wiper"):
        if word in question.lower() and word not in query.lower():
            extra.append(word)
    if extra:
        query = (query + " " + " ".join(extra)).strip()[:90]
    encoded = quote_plus(query)
    if spec:
        note = (
            "Exact type from the ingested manual. These are search pages. "
            "Check fitment yourself. We are not picking a listing."
        )
    else:
        note = (
            "The ingested manual did not name a type. Search is year, make, model, "
            "and the part. Check fitment yourself."
        )
    return {
        "spec": spec or label,
        "query": query,
        "note": note,
        "links": [{"name": name, "url": template.format(q=encoded)} for name, template in SITES],
    }
