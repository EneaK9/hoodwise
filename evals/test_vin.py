from app.vin import (
    CIVIC_VDS,
    YEAR_CODES,
    _from_vpic_row,
    _pattern,
    _wmi_only,
    apply_engine_identity,
    decode_is_complete,
    emissions_hint,
    find_vins,
    from_api_ninjas,
    from_carsxe,
    from_vincario,
    from_vincario_market,
    from_vincario_stolen,
    fuel_from_emissions,
    iso_model_year,
    pick_decode,
    vincario_control_sum,
    vpic_model_is_trusted,
)

# What Vincario actually returns for KMHSW81UBGU585399: no fuel field at all.
SANTA_FE_PAYLOAD = {
    "decode": [
        {"label": "Make", "value": "Hyundai"},
        {"label": "Model", "value": "Santa Fe"},
        {"label": "Model Year", "value": "2016"},
        {"label": "Body", "value": "Wagon"},
        {"label": "Engine Displacement (ccm)", "value": "1995"},
        {"label": "Fuel Consumption Combined (l/100km)", "value": "6"},
        {"label": "CO2 Emission (g/km)", "value": "159"},
        {"label": "VIN", "value": "KMHSW81UBGU585399"},
    ]
}


def test_fuel_from_emissions_separates_diesel_and_petrol() -> None:
    # 159 g/km at 6 l/100km is 2650 g per litre: diesel chemistry.
    assert fuel_from_emissions("159", "6") == "diesel"
    # 139 g/km at 6 l/100km is 2317 g per litre: petrol.
    assert fuel_from_emissions(139, 6.0) == "gasoline"
    # Too close to call or nonsense: say nothing.
    assert fuel_from_emissions("150", "6") is None
    assert fuel_from_emissions(None, "6") is None
    assert fuel_from_emissions("0", "6") is None


def test_decode_carries_evidence_for_the_model_not_conclusions() -> None:
    decoded = from_vincario(SANTA_FE_PAYLOAD)
    assert decoded is not None
    assert decoded["fuel"] is None  # no fuel field from Vincario, and no table guessing here
    decoded = apply_engine_identity(decoded)
    assert decoded["displacement_cc"] == 1995
    assert "diesel" in decoded["emissions_hint"]
    assert emissions_hint({"specs": [{"label": "CO2 Emission (g/km)", "value": "139"}, {"label": "Fuel Consumption Combined (l/100km)", "value": "6"}]}).startswith("CO2 per litre of fuel points to gasoline")
    assert emissions_hint({"specs": []}) is None


def test_decoder_fuel_strings_stay_raw_until_the_model_reads_them() -> None:
    decoded = from_vincario({
        "decode": [
            {"label": "Make", "value": "Hyundai"},
            {"label": "Model", "value": "Santa Fe"},
            {"label": "Model Year", "value": "2016"},
            {"label": "Fuel Type", "value": "Diesel"},
        ]
    })
    assert decoded is not None
    assert decoded["fuel"] is None
    assert decoded["fuel_raw"] == "Diesel"


def test_user_confirmation_is_the_only_fuel_set_in_code() -> None:
    decoded = from_vincario(SANTA_FE_PAYLOAD)
    assert decoded is not None
    decoded["fuel"] = "gasoline"
    decoded["fuel_source"] = "user"
    out = apply_engine_identity(decoded)
    assert out["fuel"] == "gasoline" and out["fuel_source"] == "user"


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
                {"label": "Fuel Type", "value": "Diesel"},
                {"label": "VIN", "value": "KMHSW81UBGU585399"},
            ]
        }
    )
    assert decoded is not None
    assert decoded["make"] == "Hyundai"
    assert decoded["model"] == "Santa Fe"
    assert decoded["year"] == 2016
    assert decoded["engine_label"] == "2.0L"
    assert decoded["fuel"] is None
    assert decoded["fuel_raw"] == "Diesel"
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
