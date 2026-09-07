"""Shop search links for the item the verified answer names. Search pages only.

The item text comes from the verification model. Code does exactly two things: builds
allowlisted retailer search URLs for the configured region, and drops hosts that are down
or whose search path 404s (checked once per six hours). Nothing is parsed out of text here.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from app.config import settings

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
    "www.ebay.com", "www.ebay.co.uk", "www.ebay.de",
    "www.amazon.com", "www.amazon.de", "www.amazon.co.uk",
    "www.autodoc.co.uk", "www.autodoc.de",
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


def shop_links(item: str | None, source_note: str = "") -> dict[str, Any] | None:
    """`item` is the verification model's search-ready description of the purchasable part."""
    item = (item or "").strip()
    if not item:
        return None
    links = pick_shop_links(item[:120])
    if not links:
        return None
    return {
        "spec": item,
        "query": item,
        "source": "answer",
        "note": (
            source_note
            or "Search pages for the item named in the answer, on shops that answered our check. "
            "Listings are not verified for fitment. Check yourself."
        ),
        "links": links,
    }
