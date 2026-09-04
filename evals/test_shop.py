from app.shop import extract_spec, shop_links


def test_shop_bulb_uses_manual_type() -> None:
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


def test_shop_skips_torque() -> None:
    assert shop_links("rear caliper bleed screw torque", {"specs": [], "chunks": []}, None) is None


def test_shop_without_exact_type_still_links() -> None:
    shop = shop_links(
        "what fog bulb does this take",
        {"specs": [], "chunks": []},
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe"},
        answer="The owner's manual does not specify the fog bulb wattage or part number.",
    )
    assert shop is not None
    assert len(shop["links"]) == 3
    assert "Santa Fe" in shop["query"]
    assert "fog" in shop["query"].lower() or "bulb" in shop["query"].lower()


def test_oil_viscosity_from_manual() -> None:
    spec = extract_spec(
        "what engine oil should I use",
        {"specs": [], "chunks": [{"content": "SAE 5W-20 API SM / ILSAC GF-4"}]},
    )
    assert spec is not None
    assert "5W-20" in spec
    assert "API SM" in spec
