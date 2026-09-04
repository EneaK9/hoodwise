from app.answer import _fluid_windows
from app.resolve import clean_part_name, infer_hints_from_decode, infer_hints_from_text
from app.retrieval import (
    _is_capacity_query,
    _keywords,
    _merge_page_chunks,
    _ts_or_query,
    filter_specs_for_hints,
)


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
