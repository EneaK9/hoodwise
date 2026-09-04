from app.vin import (
    CIVIC_VDS,
    YEAR_CODES,
    _from_vpic_row,
    _pattern,
    _wmi_only,
    decode_is_complete,
    find_vins,
    from_api_ninjas,
    from_carsxe,
    from_vincario,
    from_vincario_market,
    from_vincario_stolen,
    iso_model_year,
    pick_decode,
    vincario_control_sum,
    vpic_model_is_trusted,
)


def test_pattern_us_civic_15t() -> None:
    decoded = _pattern("19XFC1F79HE028859")
    assert decoded is not None
    assert decoded["model"] == "Civic"
    assert decoded["engine_label"] == "1.5T"
    assert decoded["year"] == 2017


def test_pattern_type_r() -> None:
    decoded = _pattern("SHHFK8H11JU000001")
    assert decoded is not None
    assert "Type-R" in decoded["engine_label"]


def test_wmi_unknown_still_returns_country() -> None:
    decoded = _wmi_only("JHMFC1F79HX000001")
    assert decoded["source"] == "wmi"
    assert decoded["make"] == "Honda"


def test_year_codes_cover_gen10() -> None:
    assert YEAR_CODES["G"] == 2016
    assert YEAR_CODES["M"] == 2021


def test_vds_table_not_empty() -> None:
    assert len(CIVIC_VDS) >= 8


def test_find_vins_optional_in_chat() -> None:
    assert find_vins("no vin here") == []
    assert find_vins("check 2HGFC1F59JH000001 please") == ["2HGFC1F59JH000001"]


def test_iso_year_g_is_2016_not_1986() -> None:
    assert iso_model_year("KMHSW81UBGU585399", now=2026) == 2016


def test_wmi_hyundai() -> None:
    decoded = _wmi_only("KMHSW81UBGU585399")
    assert decoded["make"] == "Hyundai"
    assert decoded["year"] == 2016
    assert decoded["model"] is None


def test_vpic_rejects_stellar_guess() -> None:
    row = {
        "Make": "HYUNDAI",
        "Model": "Stellar",
        "ModelYear": "1986",
        "ErrorCode": "1,5,14,400",
        "EngineModel": "",
        "TransmissionStyle": "",
        "Trim": "",
        "BodyClass": "",
        "PlantCountry": "SOUTH KOREA",
    }
    assert vpic_model_is_trusted(row) is False
    decoded = _from_vpic_row(row, "KMHSW81UBGU585399")
    assert decoded is not None
    assert decoded["model"] is None
    assert decoded["year"] == 2016
    assert decoded["make"] == "HYUNDAI"
    assert decoded["confidence"] == "partial"
    assert decode_is_complete(decoded) is False


def test_vincario_control_sum_matches_docs() -> None:
    assert (
        vincario_control_sum(endpoint_id="decode", key="aaa", secret="bbb", vin="WVWZZZ3CZWE123456")
        == __import__("hashlib").sha1(b"WVWZZZ3CZWE123456|decode|aaa|bbb").hexdigest()[:10]
    )
    assert (
        vincario_control_sum(endpoint_id="balance", key="aaa", secret="bbb")
        == __import__("hashlib").sha1(b"balance|aaa|bbb").hexdigest()[:10]
    )


def test_vincario_stolen_and_market_parsers() -> None:
    stolen = from_vincario_stolen(
        {"stolen": [{"code": "cz", "status": "not-stolen"}, {"code": "ro", "status": "not-stolen"}]}
    )
    assert stolen[0] == {"region": "Czechia", "status": "not stolen"}
    market = from_vincario_market(
        {
            "market_price": {"europe": {"price_median": 17945, "price_below": 13800, "price_above": 22200, "price_currency": "EUR", "price_count": 42}},
            "market_odometer": {"europe": {"odometer_median": 124118}},
            "records": [{"market": "DE"}, {"market": "IT"}],
        }
    )
    assert market is not None
    assert market["median"] == 17945
    assert market["markets"] == ["DE", "IT"]


def test_vincario_error_payload_is_rejected() -> None:
    assert from_vincario({"error": True, "message": "Invalid Control sum"}) is None


def test_vincario_payload_maps_make_model() -> None:
    decoded = from_vincario(
        {
            "decode": [
                {"label": "Make", "value": "Hyundai"},
                {"label": "Model", "value": "Santa Fe"},
                {"label": "Model Year", "value": "2016"},
                {"label": "Body", "value": "SUV"},
                {"label": "Engine Displacement (ccm)", "value": "1995"},
                {"label": "VIN", "value": "KMHSW81UBGU585399"},
            ]
        }
    )
    assert decoded is not None
    assert decoded["make"] == "Hyundai"
    assert decoded["model"] == "Santa Fe"
    assert decoded["year"] == 2016
    assert decoded["engine_label"] == "2.0L"
    assert any(s["label"] == "Engine Displacement (ccm)" for s in decoded["specs"])
    assert all(s["label"] != "VIN" for s in decoded["specs"])
    assert decode_is_complete(decoded)


def test_carsxe_and_ninjas_require_model() -> None:
    assert from_carsxe({"success": True, "make": "Hyundai", "year": 2016}) is None
    decoded = from_carsxe({"success": True, "make": "Hyundai", "model": "Santa Fe", "year": "2016"})
    assert decoded is not None and decoded["model"] == "Santa Fe"
    assert from_api_ninjas({"manufacturer": "HYUNDAI", "year": 2016}) is None


def test_pick_decode_uses_global_api_when_vpic_untrusted(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.vin._vpic",
        lambda vin: _from_vpic_row(
            {
                "Make": "HYUNDAI",
                "Model": "Stellar",
                "ModelYear": "1986",
                "ErrorCode": "1,5,14,400",
            },
            vin,
        ),
    )
    monkeypatch.setattr("app.vin._vincario", lambda vin: None)
    monkeypatch.setattr(
        "app.vin._carsxe",
        lambda vin: from_carsxe({"success": True, "make": "Hyundai", "model": "Santa Fe", "year": 2016}),
    )
    monkeypatch.setattr("app.vin._api_ninjas", lambda vin: None)
    monkeypatch.setattr("app.vin._pattern", lambda vin: None)
    decoded = pick_decode("KMHSW81UBGU585399")
    assert decoded["source"] == "carsxe"
    assert decoded["model"] == "Santa Fe"
    assert decoded["year"] == 2016


def test_pick_decode_keeps_provider_error_in_note(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.vin._vpic",
        lambda vin: _from_vpic_row(
            {"Make": "HYUNDAI", "Model": "Stellar", "ModelYear": "1986", "ErrorCode": "1,5,14,400"},
            vin,
        ),
    )
    monkeypatch.setattr("app.vin._vincario", lambda vin: {"_error": "Vincario: Invalid Control sum"})
    monkeypatch.setattr("app.vin._carsxe", lambda vin: None)
    monkeypatch.setattr("app.vin._api_ninjas", lambda vin: None)
    monkeypatch.setattr("app.vin._pattern", lambda vin: None)
    decoded = pick_decode("KMHSW81UBGU585399")
    assert decoded["model"] is None
    assert "Invalid Control sum" in (decoded.get("note") or "")
