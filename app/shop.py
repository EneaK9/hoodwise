"""Exact-spec shopping searches. We do not scrape or pick a listing."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse, quote_plus

SHOP_INTENT = re.compile(
    r"light\s*bul[bd]s?|headlights?|\bbulbs?\b|\blamps?\b|\blights?\b|"
    r"\bfog\b|\btire|\btyre|\boil\b|\bfilter|\bwiper|\bfluid|\bcoolant|"
    r"\bbattery|\bfuse|\bwasher",
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
OIL_SPEC_RE = re.compile(
    r"\b(?:API(?:\s+Service)?\s+S[LMNP](?:\s+PLUS)?|ILSAC\s+GF-\d|ACEA\s+(?:A[35]|B[34]|C[2-5]))\b",
    re.IGNORECASE,
)
GASOLINE_OIL_RE = re.compile(r"\b(?:ILSAC|API(?:\s+Service)?\s+S[LM])\b", re.IGNORECASE)
ATF_RE = re.compile(r"\b(?:ATF\s+SP-?IV|SP-?IV|PSF-4)\b", re.IGNORECASE)
SAFE_QUERY = re.compile(r"[^A-Za-z0-9./\-\s]+")
FLUID_KINDS = {"engine oil", "coolant", "ATF"}
PROBE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# First working URL per retailer is kept. autodoc.al is dead (connection refused).
SITES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "eBay",
        (
            "https://www.ebay.co.uk/sch/i.html?_nkw={q}",
            "https://www.ebay.com/sch/i.html?_nkw={q}",
        ),
    ),
    (
        "Amazon",
        (
            "https://www.amazon.com/s?k={q}",
            "https://www.amazon.de/s?k={q}",
        ),
    ),
    (
        "Autodoc",
        (
            "https://www.autodoc.co.uk/search?keyword={q}",
            "https://www.autodoc.de/search?keyword={q}",
        ),
    ),
)

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


def _blob(question: str, retrieved: dict[str, Any], answer: str) -> str:
    parts = [question, answer]
    for spec in retrieved.get("specs") or []:
        parts.append(" ".join(str(spec.get(k) or "") for k in ("value_raw", "part_name", "raw_context")))
    for chunk in retrieved.get("chunks") or []:
        parts.append(chunk.get("content") or "")
    return "\n".join(parts)


def _kind(question: str) -> str:
    q = question.lower().replace("lightbuld", "lightbulb")
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
    if re.search(r"lightbulb|headlight|\bbulbs?\b|\blamps?\b|\bfog\b|\blights?\b", q):
        return "bulb"
    return ""


NO_SPEC = re.compile(
    r"does not specify|does not list|no (?:bulb )?type|not specify a bulb|"
    r"cannot quote|do not have|lacks a|no .*part number|no .*wattage",
    re.IGNORECASE,
)


def _diesel_windows(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.search(r"diesel|dpf|acea\s+c", line, re.I):
            lines.append(line)
    return "\n".join(lines)


def extract_spec(
    question: str,
    retrieved: dict[str, Any],
    answer: str = "",
    vehicle: dict[str, Any] | None = None,
) -> str | None:
    if answer and NO_SPEC.search(answer):
        return None
    text = _blob(question, retrieved, answer)
    kind = _kind(question)
    fuel = str((vehicle or {}).get("fuel") or "").lower()
    if kind == "engine oil" and fuel not in {"diesel", "gasoline"}:
        disp = str((vehicle or {}).get("displacement_l") or (vehicle or {}).get("engine_label") or "")
        disp_m = re.search(r"(\d+\.\d+)", disp)
        if disp_m and re.search(r"\bdiesel\b", text, re.I):
            num = re.escape(disp_m.group(1))
            labeled = bool(re.search(rf"diesel.{{0,80}}{num}|{num}\s*/\s*\d+\.\d+", text, re.I))
            dpf_row = bool(re.search(r"diesel engine with dpf", text, re.I))
            gas_same = bool(re.search(rf"gasoline.{{0,40}}{num}", text, re.I))
            if (labeled or dpf_row) and not gas_same:
                fuel = "diesel"
    if kind == "tire":
        hit = TIRE_RE.search(text)
        return hit.group(0).upper() if hit else None
    if kind in {"engine oil", "coolant"}:
        search_in = text
        if kind == "engine oil" and fuel == "diesel":
            diesel_text = _diesel_windows(text)
            vis = VISCOSITY_RE.search(diesel_text) if diesel_text else None
            spec = OIL_SPEC_RE.search(diesel_text) if diesel_text else None
            if spec and GASOLINE_OIL_RE.search(spec.group(0)) and not re.search(r"ACEA\s+C", spec.group(0), re.I):
                spec = None
            bits = [p.group(0) for p in (vis, spec) if p]
            bits.append("diesel")
            if re.search(r"\bdpf\b", text, re.I):
                bits.append("DPF")
            return " ".join(bits)
        vis = VISCOSITY_RE.search(search_in)
        spec = OIL_SPEC_RE.search(search_in)
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
    kind = _kind(question)
    parts: list[str] = []
    # Year/make/model on fluid searches turns eBay into car listings.
    if kind not in FLUID_KINDS:
        parts.extend(_vehicle_bits(vehicle))
    if spec:
        parts.append(spec)
    if kind and kind.lower() not in (spec or "").lower():
        parts.append(kind)
    raw = SAFE_QUERY.sub(" ", " ".join(parts))
    return re.sub(r"\s+", " ", raw).strip()[:90]


def web_part_guess(vehicle: dict[str, Any] | None, kind: str) -> tuple[str | None, str]:
    """Best-effort type from public web snippets. Not a manual fact."""
    bits = _vehicle_bits(vehicle) + [kind or "bulb", "type"]
    research = "https://duckduckgo.com/?q=" + quote_plus(" ".join(bits))
    html_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(" ".join(bits))
    try:
        import httpx

        resp = httpx.get(
            html_url,
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 HoodwiseLookup/1.0"},
        )
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
    """Live check: keep bot-walled search pages; drop dead hosts and homepage redirects."""
    if not shop_url_allowed(url):
        return False
    try:
        import httpx

        resp = httpx.get(
            url,
            timeout=6.0,
            follow_redirects=True,
            headers=PROBE_HEADERS,
        )
    except Exception:
        return False
    if resp.status_code == 404:
        return False
    if resp.status_code not in {200, 202, 204, 301, 302, 303, 307, 308, 403}:
        return False
    final = str(resp.url)
    if _still_search_url(final):
        return True
    # eBay/Autodoc often 403 on the original search URL and never rewrite it.
    if resp.status_code == 403 and shop_url_allowed(url):
        return True
    return False


def validate_shop_url(url: str) -> bool:
    return shop_url_allowed(url) and probe_shop_url(url)


def pick_shop_links(query: str) -> list[dict[str, str]]:
    encoded = quote_plus(query)
    links: list[dict[str, str]] = []
    for name, templates in SITES:
        for template in templates:
            url = template.format(q=encoded)
            if validate_shop_url(url):
                links.append({"name": name, "url": url})
                break
    return links


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
    spec = extract_spec(question, retrieved, answer, vehicle)
    source = "manual" if spec else "search"
    research = None
    kind = _kind(question)
    if not spec and kind:
        guessed, research = web_part_guess(vehicle, kind)
        if guessed:
            spec = guessed
            source = "web"
    label = spec or kind
    query = search_query(label, question, vehicle)
    if not query:
        return None
    extra = []
    for word in ("fog", "headlight", "cabin", "wiper"):
        if word in question.lower() and word not in query.lower():
            extra.append(word)
    if extra:
        query = (query + " " + " ".join(extra)).strip()[:90]
    if source == "manual":
        note = (
            "Exact type from the ingested manual. These are search pages we checked. "
            "Check fitment yourself. We are not picking a listing."
        )
    elif source == "web":
        note = (
            "Not in the ingested manual. Type seen on the web (unverified): "
            f"{spec}. Check fitment yourself."
        )
    else:
        note = (
            "The ingested manual did not name a type. Search is year, make, model, "
            "and the part. Check fitment yourself."
        )
    links = pick_shop_links(query)
    if research:
        links.append({"name": "Web lookup", "url": research})
    if not links:
        return None
    return {
        "spec": spec or label,
        "query": query,
        "source": source,
        "note": note,
        "links": links,
    }
