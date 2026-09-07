import pytest

from app.shop import _HOST_STATUS, pick_shop_links, probe_shop_url, shop_links, shop_url_allowed


@pytest.fixture(autouse=True)
def _fresh_host_cache():
    _HOST_STATUS.clear()
    yield
    _HOST_STATUS.clear()


def test_shop_url_allowlist() -> None:
    assert shop_url_allowed("https://www.ebay.co.uk/sch/i.html?_nkw=5W-30+engine+oil")
    assert shop_url_allowed("https://www.ebay.de/sch/i.html?_nkw=5W-30+engine+oil")
    assert shop_url_allowed("https://www.amazon.com/s?k=5W-30+engine+oil")
    assert shop_url_allowed("https://www.autodoc.de/search?keyword=5W-30+engine+oil")
    assert not shop_url_allowed("https://www.autodoc.al/search?keyword=5W-30")
    assert not shop_url_allowed("https://www.autodoc.com/search?keyword=5W-30")
    assert not shop_url_allowed("https://www.amazon.com/")
    assert not shop_url_allowed("http://www.ebay.com/sch/i.html?_nkw=oil")
    assert not shop_url_allowed("https://evil.example/sch/i.html?_nkw=oil")


def test_links_come_from_the_verified_item_only(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: True)
    shop = shop_links("5W-30 ACEA C3 diesel engine oil")
    assert shop is not None
    assert [l["name"] for l in shop["links"]] == ["eBay", "Amazon", "Autodoc"]
    assert all("5W-30+ACEA+C3+diesel+engine+oil" in l["url"] for l in shop["links"])
    assert all(".de/" in l["url"] for l in shop["links"])
    assert shop_links(None) is None
    assert shop_links("   ") is None


def test_region_switch_uses_uk_sites(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: True)
    monkeypatch.setattr("app.shop.settings.shop_region", "uk")
    links = pick_shop_links("H7 bulb")
    assert [l["name"] for l in links] == ["eBay", "Amazon", "Autodoc"]
    assert all(".co.uk" in l["url"] for l in links)


def test_pick_skips_failed_retailer(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: "autodoc" not in url)
    assert [l["name"] for l in pick_shop_links("5W-30 engine oil")] == ["eBay", "Amazon"]


def test_host_check_is_cached_per_host(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: calls.append(url) or True)
    pick_shop_links("5W-30 engine oil")
    pick_shop_links("H7 bulb")
    assert len(calls) == 3


def test_probe_rejects_dead_host_and_404(monkeypatch) -> None:
    def boom(url, **kwargs):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr("httpx.get", boom)
    assert probe_shop_url("https://www.autodoc.de/search?keyword=oil") is False

    class NotFound:
        status_code = 404
        url = "https://www.autodoc.de/search?keyword=oil"

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: NotFound())
    assert probe_shop_url("https://www.autodoc.de/search?keyword=oil") is False


def test_probe_rejects_homepage_redirect_and_keeps_bot_wall(monkeypatch) -> None:
    class Home:
        status_code = 200
        url = "https://www.amazon.de/"

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: Home())
    assert probe_shop_url("https://www.amazon.de/s?k=oil") is False

    class Wall:
        status_code = 403
        url = "https://www.ebay.de/sch/i.html?_nkw=oil"

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: Wall())
    assert probe_shop_url("https://www.ebay.de/sch/i.html?_nkw=oil") is True
