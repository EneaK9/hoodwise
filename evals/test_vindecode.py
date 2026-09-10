from app import vindecode as vd


def test_check_digit_and_format() -> None:
    assert vd.valid_format("19XFC1F79HE028859")
    assert not vd.valid_format("19XFC1F79HE02885O")  # letter O is not allowed
    # 1HGBH41JXMN109186 is the textbook example VIN whose check digit is X
    assert vd.check_digit("1HGBH41JXMN109186") == "X"
    assert vd.is_north_american("19XFC1F79HE028859")
    assert not vd.is_north_american("KMHSW81UBGU585399")


def test_year_code_cycle() -> None:
    assert vd.year_from_code("KMHSW81UBGU585399", now=2026) == 2016
    assert vd.year_from_code("XXXXXXXXXAXXXXXXX", now=2026) == 2010
    assert vd.year_from_code("XXXXXXXXXTXXXXXXX", now=2026) == 2026
    assert vd.year_from_code("XXXXXXXXXXXXXXXXX", now=2026) == 1999  # X would be 2029: in the future, so the previous cycle
    assert vd.year_from_code("XXXXXXXXX0XXXXXXX", now=2026) is None


def test_pattern_skips_check_digit_position() -> None:
    assert vd.pattern("KMHSW81UBGU585399") == "KMHSW81U_GU"
    assert vd.pattern("KMHSW81U5GU000001") == "KMHSW81U_GU"


def test_key_decode_filters_by_market_and_matches_codes(monkeypatch) -> None:
    rows = [
        {"position_from": 4, "position_to": 4, "field": "model", "code": "S", "value": "Santa Fe", "market_scope": "all", "attrs": None, "source_doc": "DM", "source_page": 7, "model_line": "Santa Fe", "market": None},
        {"position_from": 9, "position_to": 9, "field": "drive_transmission", "code": "B", "value": "LHD & AT", "market_scope": "outside_north_america", "attrs": {"transmission": "AT", "drive_side": "LHD"}, "source_doc": "DM", "source_page": 7, "model_line": "Santa Fe", "market": "Except U.S.A"},
        {"position_from": 9, "position_to": 9, "field": "check_digit", "code": None, "value": "0-9, X", "market_scope": "north_america", "attrs": None, "source_doc": "DM", "source_page": 7, "model_line": "Santa Fe", "market": "For U.S.A"},
        {"position_from": 8, "position_to": 8, "field": "engine", "code": "A", "value": "Gasoline engine 2.4 (Theta-II GDI)", "market_scope": "all", "attrs": {"fuel": "gasoline"}, "source_doc": "DM", "source_page": 7, "model_line": "Santa Fe", "market": None},
    ]
    monkeypatch.setattr(vd, "fetch_all", lambda sql, params=None: rows)
    out = vd.key_decode("KMHSW81UBGU585399")
    assert out["model"][0]["value"] == "Santa Fe"
    assert out["drive_transmission"][0]["attrs"]["transmission"] == "AT"
    assert "check_digit" not in out  # North-America-only row dropped for a Korean-market VIN
    assert "engine" not in out  # code U is not in the key: nothing asserted


def test_identify_precedence_and_unknowns(monkeypatch) -> None:
    monkeypatch.setattr(vd, "structure", lambda vin: {"wmi": "KMH", "region_north_america": False, "serial": "585399", "make": "HYUNDAI", "manufacturer": "HYUNDAI MOTOR CO", "country": "SOUTH KOREA", "year": 2016, "check_digit_ok": None})
    monkeypatch.setattr(vd, "vpic_decode", lambda vin: {"fields": {"model": "Stellar", "year": "1986"}, "error_codes": [1, 5, 14, 400], "trusted": False})
    monkeypatch.setattr(vd, "key_decode", lambda vin: {"model": [{"value": "Santa Fe", "code": "S", "attrs": {}, "source_doc": "DM", "source_page": 7}], "drive_transmission": [{"value": "LHD & AT", "code": "B", "attrs": {"transmission": "AT", "drive_side": "LHD"}, "source_doc": "DM", "source_page": 7}]})
    monkeypatch.setattr(vd, "confirmations", lambda vin: ({"fuel": {"value": "diesel", "source": "user"}}, {"engine": {"value": "2.0 CRDi", "count": 3}}))
    ident = vd.identify("KMHSW81UBGU585399", {"source": "vincario", "make": "Hyundai", "model": "Santa Fe", "year": 2016, "transmission": "Any"})
    f = ident["fields"]
    assert f["model"] == {"value": "Santa Fe", "source": "key", "basis": "VIN key in DM p.7, code S"}
    assert f["transmission"]["value"] == "AT" and f["transmission"]["source"] == "key"
    assert f["fuel"] == {"value": "diesel", "source": "user", "basis": "confirmed for this VIN"}
    assert f["engine"]["source"] == "learned" and "3 confirmed cars" in f["engine"]["basis"]
    assert f["year"]["value"] == 2016 and f["year"]["source"] == "provider"
    assert "Stellar" not in str(f)  # untrusted vPIC decode never asserts
    assert ident["unknown"] == []
    lines = vd.basis_lines(ident)
    assert lines[0].startswith("model: Santa Fe (VIN key")
