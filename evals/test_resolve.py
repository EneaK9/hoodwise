from app.answer import _fluid_windows
from app.resolve import (
    apply_manual_fuel,
    clean_part_name,
    infer_fuel,
    infer_hints_from_decode,
    infer_hints_from_text,
    manual_fuel_from_text,
    public_vehicle,
    vehicle_label,
)
from app.retrieval import (
    _is_capacity_query,
    _keywords,
    _merge_page_chunks,
    _ts_or_query,
    filter_specs_for_hints,
    score_capacity_chunk,
    search_vehicle_id,
)


def test_catalog_placeholder_is_not_an_engine_label() -> None:
    label = vehicle_label({"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "Owner manual", "transmission": "Any"})
    assert label == "2016 Hyundai Santa Fe"


def test_no_vehicle_means_no_filter_not_civic() -> None:
    assert search_vehicle_id(None) is None
    assert search_vehicle_id("abc") == "abc"


def test_public_vehicle_flags_unknown_fuel_for_confirmation() -> None:
    pub = public_vehicle(
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0L", "displacement_l": "2.0", "specs": []}
    )
    assert pub is not None
    # 2.0 L with no cc: no engine-size match, no emissions, so ask the owner.
    assert pub["fuel"] is None
    assert pub["needs_fuel_confirmation"] is True
    pinned = public_vehicle(
        {"year": 2016, "make": "Hyundai", "model": "Santa Fe", "displacement_cc": 1995, "specs": []}
    )
    assert pinned is not None
    assert pinned["fuel"] == "diesel"
    assert pinned["fuel_source"] == "engine-size"
    assert pinned["engine_family"] == "2.0 CRDi"
    assert pinned["needs_fuel_confirmation"] is False
    assert "2.0 CRDi diesel" in pinned["label"]


def test_clean_part_prefers_known_phrase() -> None:
    name = clean_part_name(
        "to Specified Torque Value Note A Front brake caliper (Except Type-R) Brake hose",
        "Front brake caliper (Except Type-R) Brake hose 35 N·m",
    )
    assert name == "brake hose"


def test_hints_from_question() -> None:
    assert "typer" in infer_hints_from_text("torque on a Type-R bleed screw")
    assert "1.5" in infer_hints_from_text("1.5 L axle nut")


def test_hints_from_decode() -> None:
    hints = infer_hints_from_decode({"engine_label": "1.5T", "trim": "EX-L"})
    assert "1.5" in hints


def test_diesel_hint_from_decode() -> None:
    hints = infer_hints_from_decode({"engine_label": "2.0L CRDi", "fuel": "diesel"})
    assert "diesel" in hints
    assert "2.0" in hints
    assert infer_fuel({"specs": [{"label": "Fuel Type", "value": "Diesel"}]}) == "diesel"
    pub = public_vehicle({"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0L", "fuel": "diesel"})
    assert pub is not None
    assert pub["fuel"] == "diesel"
    assert "diesel" in (vehicle_label({"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0L", "fuel": "diesel"}) or "").lower()


def test_pin_diesel_when_manual_has_matching_row() -> None:
    vehicle = {"engine_label": "2.0L", "displacement_l": "2.0", "label": "2016 Hyundai Santa Fe 2.0L"}
    apply_manual_fuel(
        vehicle,
        {"chunks": [{"content": "Diesel Engine with DPF\nDiesel 2.0/2.2L\n6.3 l"}]},
    )
    assert vehicle["fuel"] == "diesel"
    assert vehicle["fuel_source"] == "manual"
    assert "diesel" in vehicle["label"].lower()
    # Real chunk layout: every word of the label on its own line.
    dpf_only = {"engine_label": "2.0L", "displacement_l": "2.0", "label": "2016 Hyundai Santa Fe 2.0L"}
    apply_manual_fuel(
        dpf_only,
        {"chunks": [{"content": "5.7 l (6.02 US qt.)\nDiesel\nEngine\nwith DPF *6\n6.3 l (6.66 US qt.)"}]},
    )
    assert dpf_only["fuel"] == "diesel"
    assert manual_fuel_from_text("Diesel\nEngine\nwith DPF *6\n6.3 l", "2.0") == "diesel"
    assert manual_fuel_from_text("Gasoline Engine 2.0L 4.8 l", "2.0") is None
    gas_24 = {"engine_label": "2.4L", "displacement_l": "2.4", "label": "2016 Hyundai Santa Fe 2.4L"}
    apply_manual_fuel(
        gas_24,
        {"chunks": [{"content": "Gasoline 2.4L\n4.8 l\nDiesel Engine with DPF\n6.3 l"}]},
    )
    assert gas_24.get("fuel") is None


def test_diesel_capacity_outranks_gasoline_europe_row() -> None:
    gas = score_capacity_chunk(
        "(For Europe)\n4.8 l (5.07 US qt.)\nAPI Service SM, ILSAC GF-4 or above",
        ["2.0", "diesel"],
    )
    diesel = score_capacity_chunk(
        "Diesel Engine with DPF *6\n6.3 l (6.66 US qt.)",
        ["2.0", "diesel"],
    )
    assert diesel > gas


def test_filter_drops_except_typer_when_car_is_typer() -> None:
    rows = [
        {"part_name": "brake hose", "condition_note": "Except Type-R", "raw_context": "Except Type-R", "value_raw": "35"},
        {"part_name": "brake line", "condition_note": "Type-R", "raw_context": "Type-R", "value_raw": "15"},
    ]
    kept = filter_specs_for_hints(rows, ["typer"])
    assert len(kept) == 1
    assert kept[0]["value_raw"] == "15"


def test_oil_question_keeps_engine_oil_terms() -> None:
    keys = _keywords("how much engine oil does this car take")
    assert "engine" in keys
    assert "oil" in keys
    assert "car" not in keys
    assert "take" not in keys
    assert " | " in _ts_or_query("how much engine oil does this car take")
    assert _is_capacity_query("how much engine oil does this car take")
    merged = _merge_page_chunks(
        [
            {"id": "a", "doc_id": "HY", "page_number": 792, "content": "Engine oil"},
            {"id": "b", "doc_id": "HY", "page_number": 792, "content": "4.8 l (5.07 US qt.)"},
        ]
    )
    assert len(merged) == 1
    assert "4.8 l" in merged[0]["content"]


def test_fluid_windows_keep_nearby_labels() -> None:
    windows = _fluid_windows(
        {
            "chunks": [
                {
                    "page_number": 792,
                    "content": "API Service SM *5, ILSAC GF-4\n4.8 l (5.07 US qt.) *4\nMICHANG ATF SP-IV\n7.8 l (8.24 US qt.)",
                }
            ]
        }
    )
    blob = " ".join(windows)
    assert "4.8 l" in blob
    assert "API Service SM" in blob
    assert "ATF" in blob
