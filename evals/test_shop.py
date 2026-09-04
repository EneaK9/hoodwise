from app.shop import (
    extract_spec,
    pick_shop_links,
    probe_shop_url,
    search_query,
    shop_links,
    shop_url_allowed,
)


def test_shop_bulb_uses_manual_type(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: True)
    retrieved = {
        "specs": [],
        "chunks": [{"content": "Cornering lights *\n35\nH8L\nDaytime running lights"}],
    }
    vehicle = {"year": 2016, "make": "Hyundai", "model": "Santa Fe"}
    shop = shop_links("what fog light bulb does this take", retrieved, vehicle)
    assert shop is not None
    assert shop["spec"] == "H8L"
    assert "H8L" in shop["query"]
    assert "2016" in shop["query"]
    names = [link["name"] for link in shop["links"]]
    assert names == ["eBay", "Amazon", "Autodoc"]
    assert all("H8L" in link["url"] for link in shop["links"])
    assert "ebay.co.uk" in shop["links"][0]["url"]
    assert "amazon.com" in shop["links"][1]["url"]
    assert "autodoc.co.uk" in shop["links"][2]["url"]
    assert "autodoc.al" not in shop["links"][2]["url"]


def test_shop_skips_torque() -> None:
    assert shop_links("rear caliper bleed screw torque", {"specs": [], "chunks": []}, None) is None


def test_shop_without_exact_type_still_links(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: True)
    monkeypatch.setattr("app.shop.web_part_guess", lambda *a, **k: (None, "https://duckduckgo.com/?q=test"))
    shop = shop_links(
        "what fog bulb does this take",
        {"specs": [], "chunks": []},
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe"},
        answer="The owner's manual does not specify the fog bulb wattage or part number.",
    )
    assert shop is not None
    assert [link["name"] for link in shop["links"][:3]] == ["eBay", "Amazon", "Autodoc"]
    assert "Santa Fe" in shop["query"]
    assert "fog" in shop["query"].lower() or "bulb" in shop["query"].lower()


def test_lightbuld_typo_still_shops(monkeypatch) -> None:
    monkeypatch.setattr("app.shop.probe_shop_url", lambda url: True)
    monkeypatch.setattr("app.shop.web_part_guess", lambda *a, **k: (None, "https://duckduckgo.com/?q=test"))
    shop = shop_links(
        "what lightbuld are needed for my car",
        {"specs": [], "chunks": []},
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe"},
        answer="does not list specific bulb part numbers",
    )
    assert shop is not None
    assert shop["links"]


def test_oil_viscosity_from_manual() -> None:
    spec = extract_spec(
        "what engine oil should I use",
        {"specs": [], "chunks": [{"content": "SAE 5W-20 API SM / ILSAC GF-4"}]},
    )
    assert spec is not None
    assert "5W-20" in spec
    assert "API SM" in spec


def test_oil_search_is_fluid_not_the_car() -> None:
    query = search_query(
        "5W-30 ACEA C3",
        "what type of oil is needed for my car",
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe"},
    )
    assert "2016" not in query
    assert "Hyundai" not in query
    assert "Santa Fe" not in query
    assert "5W-30" in query
    assert "engine oil" in query


def test_diesel_oil_does_not_shop_ilsac() -> None:
    spec = extract_spec(
        "what type of oil is needed for my car",
        {
            "specs": [],
            "chunks": [
                {
                    "content": (
                        "(For Europe)\n4.8 l\nAPI Service SM, ILSAC GF-4 or above\n"
                        "Diesel Engine with DPF\n6.3 l (6.66 US qt.)\nDiesel 2.0/2.2L"
                    )
                }
            ],
        },
        vehicle={"year": 2016, "make": "Hyundai", "model": "Santa Fe", "displacement_l": "2.0", "engine_label": "2.0L"},
    )
    assert spec is not None
    assert "diesel" in spec.lower()
    assert "ILSAC" not in spec
    assert "API SM" not in spec


def test_acea_c3_extracted() -> None:
    spec = extract_spec(
        "what engine oil should I use",
        {"specs": [], "chunks": [{"content": "SAE 5W-30 ACEA C3 diesel DPF"}]},
        vehicle={"fuel": "diesel"},
    )
    assert spec is not None
    assert "5W-30" in spec
    assert "ACEA C3" in spec
    assert "diesel" in spec.lower()


def test_shop_url_allowlist() -> None:
    assert shop_url_allowed("https://www.ebay.co.uk/sch/i.html?_nkw=5W-30+engine+oil")
    assert shop_url_allowed("https://www.amazon.com/s?k=5W-30+engine+oil")
    assert shop_url_allowed("https://www.autodoc.co.uk/search?keyword=5W-30+engine+oil")
    assert not shop_url_allowed("https://www.autodoc.al/search?keyword=5W-30")
    assert not shop_url_allowed("https://www.autodoc.com/search?keyword=5W-30")
    assert not shop_url_allowed("https://www.amazon.com/")
    assert not shop_url_allowed("http://www.ebay.com/sch/i.html?_nkw=oil")


def test_probe_rejects_dead_host(monkeypatch) -> None:
    def boom(url, **kwargs):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr("httpx.get", boom)
    assert probe_shop_url("https://www.autodoc.co.uk/search?keyword=oil") is False


def test_probe_rejects_homepage_redirect(monkeypatch) -> None:
    class Resp:
        status_code = 200
        url = "https://www.amazon.com/"

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: Resp())
    assert probe_shop_url("https://www.amazon.com/s?k=oil") is False


def test_probe_keeps_bot_walled_search(monkeypatch) -> None:
    class Resp:
        status_code = 403
        url = "https://www.ebay.co.uk/sch/i.html?_nkw=oil"

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: Resp())
    assert probe_shop_url("https://www.ebay.co.uk/sch/i.html?_nkw=oil") is True


def test_pick_skips_failed_retailer(monkeypatch) -> None:
    def fake_probe(url: str) -> bool:
        return "autodoc" not in url

    monkeypatch.setattr("app.shop.probe_shop_url", fake_probe)
    links = pick_shop_links("5W-30 engine oil")
    names = [link["name"] for link in links]
    assert names == ["eBay", "Amazon"]
    assert all("autodoc.al" not in link["url"] for link in links)
