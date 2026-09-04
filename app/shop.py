"""Shop search links for a part the manual named. Search pages only; we never pick a listing.

Design:
- The part category comes from the question (or the previous turn on follow-ups) via
  app.intent, and the spec is extracted ONLY with that category's patterns. A bulb
  question can never be answered with an oil grade.
- Retailer URLs are built from an allowlist per region. A cached liveness check drops
  hosts that are down or whose search path 404s. Bot walls (403) are not a failure, but
  they also are not proof the listings match; the note says so.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from app.config import settings
from app.intent import FLUID_KINDS, part_kind
from app.resolve import manual_fuel_from_text

SKIP_INTENT = re.compile(
    r"\b(torque|n[·.]?m|lbf|tighten|clearance|procedure|how to|steps?)\b",
    re.IGNORECASE,
)
BULB_RE = re.compile(
    r"\b(?:H\d{1,2}[A-Z]?|HB[345]|HIR2|D[1-4][SR]|PSX?2[46]W|P2[17]W|PY2[17]W|W5W|W16W|"
    r"W21/?5W|WY5W|WY21W|C5W|C10W|T10|T20|T15|T4W|R5W|R10W|P21/?5W|H21W|PSY24W|"
    r"PW24W|PWY24W|9005|9006|9012|9145|7443|7440|3157|3156|1156|1157|194|168|921)\b"
)
TIRE_RE = re.compile(r"\b\d{3}/\d{2}\s?Z?R\d{2}\b", re.IGNORECASE)
VISCOSITY_RE = re.compile(r"\b(?:0W|5W|10W|15W|20W)[-/](?:16|20|30|40|50)\b", re.IGNORECASE)
OIL_SPEC_RE = re.compile(
    r"\b(?:API(?:\s+Service)?\s+(?:S[LMNP](?:\s+PLUS)?|C[HIJK]-4|CK-4)|ILSAC\s+GF-\d|"
    r"ACEA\s+(?:A[35]|B[34]|C[2-5]|A[35]/B[345]))\b",
    re.IGNORECASE,
)
GASOLINE_OIL_RE = re.compile(r"\b(?:ILSAC|API(?:\s+Service)?\s+S[LMNP]|ACEA\s+A\d)\b", re.IGNORECASE)
DIESEL_OIL_RE = re.compile(r"\b(?:ACEA\s+[BC]\d|API\s+C[HIJK]-4)\b", re.IGNORECASE)
ATF_RE = re.compile(r"\b(?:ATF\s+SP-?IV|SP-?IV|PSF-4|ATF\s+SP-?III|Dexron\s+\w+)\b", re.IGNORECASE)
COOLANT_RE = re.compile(r"\b(?:ethylene\s+glycol|OAT|HOAT|G12\+*|G13|P-OAT)\b", re.IGNORECASE)
BATTERY_RE = re.compile(r"\b(?:\d{2,3}\s?Ah|AGM|EFB|\d{3}\s?CCA)\b", re.IGNORECASE)
WIPER_RE = re.compile(r"\b(?:\d{3}\s?mm|\d{2}\s?(?:in|inch|\"))\b", re.IGNORECASE)
SAFE_QUERY = re.compile(r"[^A-Za-z0-9./\-\s+]+")
NO_SPEC = re.compile(
    r"does not specify|does not list|no (?:bulb )?type|not specify a bulb|"
    r"cannot quote|do not have|lacks a|no .*part number|no .*wattage",
    re.IGNORECASE,
)

REGION_SITES: dict[str, tuple[tuple[str, str], ...]] = {
    "eu": (
        ("eBay", "https://www.ebay.de/sch/i.html?_nkw={q}"),
        ("Amazon", "https://www.amazon.de/s?k={q}"),
        ("Autodoc", "https://www.autodoc.de/search?keyword={q}"),
    ),
    "uk": (
        ("eBay", "https://www.ebay.co.uk/sch/i.html?_nkw={q}"),
        ("Amazon", "https://www.amazon.co.uk/s?k={q}"),
        ("Autodoc", "https://www.autodoc.co.uk/search?keyword={q}"),
    ),
    "us": (
        ("eBay", "https://www.ebay.com/sch/i.html?_nkw={q}"),
        ("Amazon", "https://www.amazon.com/s?k={q}"),
    ),
}
ALLOWED_HOSTS = {
    "www.ebay.com",
    "www.ebay.co.uk",
    "www.ebay.de",
    "www.amazon.com",
    "www.amazon.de",
    "www.amazon.co.uk",
    "www.autodoc.co.uk",
    "www.autodoc.de",
}
PROBE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}
HOST_TTL_SECONDS = 6 * 3600
_HOST_STATUS: dict[str, tuple[float, bool]] = {}


def sites() -> tuple[tuple[str, str], ...]:
    region = (settings.shop_region or "eu").strip().lower()
    return REGION_SITES.get(region, REGION_SITES["eu"])


def _blob(question: str, retrieved: dict[str, Any], answer: str) -> str:
    parts = [question, answer]
    for spec in retrieved.get("specs") or []:
        parts.append(f"{spec.get('part_name','')} {spec.get('value_raw','')} {spec.get('raw_context','')}")
    for chunk in retrieved.get("chunks") or []:
        parts.append(chunk.get("content") or "")
    return "\n".join(parts)


def _kind(question: str, context: str = "") -> str:
    return part_kind(question, context)


def _diesel_windows(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if re.search(r"diesel|dpf|crdi|acea\s+[bc]\d", line, re.I)
    )


def _oil_spec(text: str, fuel: str) -> str | None:
    if fuel == "diesel":
        scoped = _diesel_windows(text)
        vis = VISCOSITY_RE.search(scoped) if scoped else None
        spec = DIESEL_OIL_RE.search(scoped or "") or OIL_SPEC_RE.search(scoped or "")
        if spec and GASOLINE_OIL_RE.search(spec.group(0)):
            spec = None
        bits = [p.group(0) for p in (vis, spec) if p]
        if not bits:
            # Fuel alone is not a manual spec. The caller builds an honest fallback.
            return None
        bits.append("diesel")
        if re.search(r"\bdpf\b", text, re.I):
            bits.append("DPF")
        return " ".join(bits)
    vis = VISCOSITY_RE.search(text)
    spec = OIL_SPEC_RE.search(text)
    if fuel == "gasoline" and spec and DIESEL_OIL_RE.search(spec.group(0)):
        spec = None
    bits = [p.group(0) for p in (vis, spec) if p]
    return " ".join(bits) if bits else None


def extract_spec(
    question: str,
    retrieved: dict[str, Any],
    answer: str = "",
    vehicle: dict[str, Any] | None = None,
    context: str = "",
) -> str | None:
    """Exact type for the asked-about part, using only that part's patterns."""
    if answer and NO_SPEC.search(answer):
        return None
    text = _blob(question, retrieved, answer)
    kind = _kind(question, context)
    if kind == "tire":
        hit = TIRE_RE.search(text)
        return hit.group(0).upper().replace(" ", "") if hit else None
    if kind == "engine oil":
        fuel = str((vehicle or {}).get("fuel") or "").lower()
        if fuel not in {"diesel", "gasoline"}:
            disp = (vehicle or {}).get("displacement_l") or (vehicle or {}).get("engine_label")
            fuel = manual_fuel_from_text(text, str(disp or "")) or ""
        return _oil_spec(text, fuel)
    if kind == "coolant":
        hit = COOLANT_RE.search(text)
        return hit.group(0) if hit else None
    if kind == "ATF":
        hit = ATF_RE.search(text)
        return hit.group(0).upper() if hit else None
    if kind == "battery":
        hit = BATTERY_RE.search(text)
        return hit.group(0) if hit else None
    if kind == "wiper blade":
        hit = WIPER_RE.search(text)
        return hit.group(0) if hit else None
    if kind == "bulb":
        counts: dict[str, int] = {}
        for match in BULB_RE.findall(text):
            key = match.upper()
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return None
        return max(counts, key=lambda k: (counts[k], len(k)))
    return None


def _vehicle_bits(vehicle: dict[str, Any] | None) -> list[str]:
    if not vehicle:
        return []
    bits = [str(vehicle.get("year") or ""), str(vehicle.get("make") or ""), str(vehicle.get("model") or "")]
    return [b for b in bits if b and b.lower() not in {"none", "any"}]


POSITION_WORDS = ("fog", "headlight", "headlamp", "low beam", "high beam", "brake", "reverse", "indicator", "cabin", "rear", "front")


def search_query(spec: str, question: str, vehicle: dict[str, Any] | None, context: str = "") -> str:
    kind = _kind(question, context)
    parts: list[str] = []
    # Fluids are generic products: the car name turns eBay into car listings.
    if kind not in FLUID_KINDS:
        parts.extend(_vehicle_bits(vehicle))
    if spec:
        parts.append(spec)
    if kind and kind.lower() not in (spec or "").lower():
        parts.append(kind)
    q_low = f"{question} {context if kind == _kind(context) else ''}".lower()
    for word in POSITION_WORDS:
        if word in q_low and word not in " ".join(parts).lower():
            parts.append(word)
    raw = SAFE_QUERY.sub(" ", " ".join(parts))
    return re.sub(r"\s+", " ", raw).strip()[:90]


def web_part_guess(vehicle: dict[str, Any] | None, kind: str) -> tuple[str | None, str]:
    """Best-effort bulb code from public web snippets. Labeled unverified in the UI."""
    bits = _vehicle_bits(vehicle) + [kind or "bulb", "type"]
    research = "https://duckduckgo.com/?q=" + quote_plus(" ".join(bits))
    if kind != "bulb":
        return None, research
    html_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(" ".join(bits))
    try:
        import httpx

        resp = httpx.get(html_url, timeout=6.0, follow_redirects=True, headers=PROBE_HEADERS)
        text = resp.text or ""
    except Exception:
        return None, research
    counts: dict[str, int] = {}
    for match in BULB_RE.findall(text):
        key = match.upper()
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, research
    best = max(counts, key=counts.get)
    if counts[best] < 2:
        return None, research
    return best, research


def shop_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_HOSTS:
        return False
    query = parse_qs(parsed.query)
    host = parsed.netloc
    path = parsed.path.rstrip("/") or "/"
    if host.startswith("www.ebay.") and path == "/sch/i.html":
        return bool(query.get("_nkw"))
    if host.startswith("www.amazon.") and path == "/s":
        return bool(query.get("k"))
    if host.startswith("www.autodoc.") and path == "/search":
        return bool(query.get("keyword"))
    return False


def _still_search_url(url: str) -> bool:
    if shop_url_allowed(url):
        return True
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    query = parse_qs(parsed.query)
    host = parsed.netloc
    if host.startswith("www.ebay.") and query.get("_nkw"):
        return True
    if host.startswith("www.amazon.") and query.get("k"):
        return True
    if host.startswith("www.autodoc.") and (query.get("keyword") or "search" in parsed.path):
        return True
    return False


def probe_shop_url(url: str) -> bool:
    """Live check of one URL. Dead host or 404 search path fails; a bot wall passes."""
    if not shop_url_allowed(url):
        return False
    try:
        import httpx

        resp = httpx.get(url, timeout=4.0, follow_redirects=True, headers=PROBE_HEADERS)
    except Exception:
        return False
    if resp.status_code == 404 or resp.status_code >= 500:
        return False
    if resp.status_code not in {200, 202, 204, 301, 302, 303, 307, 308, 403, 429}:
        return False
    if _still_search_url(str(resp.url)):
        return True
    return resp.status_code in {403, 429}


def host_alive(url: str) -> bool:
    """probe_shop_url, cached per host for HOST_TTL_SECONDS so chat never waits twice."""
    host = urlparse(url).netloc
    now = time.time()
    cached = _HOST_STATUS.get(host)
    if cached and now - cached[0] < HOST_TTL_SECONDS:
        return cached[1]
    alive = probe_shop_url(url)
    _HOST_STATUS[host] = (now, alive)
    return alive


def validate_shop_url(url: str) -> bool:
    return shop_url_allowed(url) and host_alive(url)


def pick_shop_links(query: str) -> list[dict[str, str]]:
    encoded = quote_plus(query)
    links: list[dict[str, str]] = []
    for name, template in sites():
        url = template.format(q=encoded)
        if validate_shop_url(url):
            links.append({"name": name, "url": url})
    return links


def shop_links(
    question: str,
    retrieved: dict[str, Any],
    vehicle: dict[str, Any] | None = None,
    answer: str = "",
    context: str = "",
) -> dict[str, Any] | None:
    kind = _kind(question, context)
    if not kind:
        return None
    if SKIP_INTENT.search(question) and kind not in FLUID_KINDS and kind != "bulb":
        return None
    spec = extract_spec(question, retrieved, answer, vehicle, context)
    source = "manual" if spec else "search"
    research = None
    if not spec and kind == "bulb":
        guessed, research = web_part_guess(vehicle, kind)
        if guessed:
            spec = guessed
            source = "web"
    label = spec or kind
    fuel = str((vehicle or {}).get("fuel") or "")
    if not spec and kind == "engine oil" and fuel in {"diesel", "gasoline"}:
        # No grade in the manual, but the fuel is pinned: never send a diesel to petrol oil.
        label = "diesel engine oil" if fuel == "diesel" else "petrol engine oil"
        if fuel == "diesel" and re.search(r"\bdpf\b", _blob(question, retrieved, answer), re.I):
            label += " DPF low SAPS"
    query = search_query(label, question, vehicle, context)
    if not query:
        return None
    if source == "manual":
        note = (
            f"Exact {kind} type from the ingested manual. These are search pages on shops that "
            "answered our check. Listings are not verified for fitment. Check yourself."
        )
    elif source == "web":
        note = (
            f"Not in the ingested manual. {kind.title()} type seen on the web (unverified): "
            f"{spec}. Check fitment yourself."
        )
    elif kind in FLUID_KINDS:
        note = (
            f"The ingested manual did not name an exact {kind} grade. Search is the fluid"
            + (f" for a {fuel} engine" if fuel else "")
            + ". Check the viscosity chart and fitment yourself."
        )
    else:
        note = (
            f"The ingested manual did not name an exact {kind} type. Search is year, make, model, "
            "and the part. Check fitment yourself."
        )
    links = pick_shop_links(query)
    if research:
        links.append({"name": "Web lookup", "url": research})
    if not links:
        return None
    return {
        "kind": kind,
        "spec": spec or label,
        "query": query,
        "source": source,
        "note": note,
        "links": links,
    }
