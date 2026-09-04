from app.resolve import clean_part_name, infer_hints_from_decode, infer_hints_from_text
from app.retrieval import filter_specs_for_hints


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
