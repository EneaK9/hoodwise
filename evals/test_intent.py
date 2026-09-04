from app.answer import (
    answer_conflicts,
    fluid_rows,
    fuel_conflicts,
    grade_conflicts,
    rows_need_fuel,
    strip_wrong_fuel_grades,
)
from app.intent import is_follow_up, parse_vehicle_mention, part_kind, topic_terms
from app.resolve import decoded_from_text, public_vehicle

CATALOG = [("Honda", "Civic"), ("Hyundai", "Santa Fe"), ("Hyundai", "Ioniq"), ("Hyundai", "Ioniq 5"), ("Hyundai", "I20")]


def test_vehicle_mention_reads_car_from_question() -> None:
    q = "How many litters of oil i need to chang the oil of my hyundai santa fe 2016 2.0 liter diesel"
    got = parse_vehicle_mention(q, CATALOG)
    assert got == {"make": "Hyundai", "model": "Santa Fe", "year": 2016, "displacement_l": "2.0", "fuel": "diesel"}
    assert parse_vehicle_mention("ioniq 5 tyre pressure", CATALOG)["model"] == "Ioniq 5"
    assert parse_vehicle_mention("front brake hose torque except Type-R", CATALOG) is None
    assert parse_vehicle_mention("santafe headlight bulb", CATALOG)["model"] == "Santa Fe"


def test_text_vehicle_is_labeled_as_such() -> None:
    decoded = decoded_from_text("oil for my hyundai santa fe 2016 2.0 diesel", CATALOG)
    assert decoded is not None and decoded["source"] == "text"
    pub = public_vehicle(decoded)
    assert pub is not None
    assert pub["fuel"] == "diesel" and pub["fuel_source"] == "user"
    assert pub["label"].startswith("2016 Hyundai Santa Fe")
    assert "not a VIN" in pub["note"]

# Verbatim chunk text from the 2016-2018 Santa Fe DM owner manual, page 792.
DPF_CHUNK = (
    "(Except Europe)\n4.6 l (4.86 US qt.) *3\n4.8 l (5.07 US qt.) *4\n3.3L\n"
    "5.7 l (6.02 US qt.)\nDiesel\nEngine\nwith DPF *6\n6.3 l (6.66 US qt.)"
)
EUROPE_CHUNK = "(For Europe)\n4.8 l (5.07 US qt.)\nAPI Service SM *5, ILSAC GF-4 or above"
ATF_CHUNK = "Gasoline\nEngine\n2.4L\n7.1 l (7.50 US qt.)\nMICHANG ATF SP-IV,\nSK ATF SP-IV,"


def test_part_kind_prefers_specific_phrases() -> None:
    assert part_kind("what type of oil my car need and how much to refill") == "engine oil"
    assert part_kind("what lamps does my car use") == "bulb"
    assert part_kind("which headlight bulb fits") == "bulb"
    assert part_kind("what lightbuld are needed for my car") == "bulb"
    assert part_kind("transmission fluid capacity") == "ATF"
    assert part_kind("what tyres does it take") == "tire"
    assert part_kind("front brake caliper torque") == ""


def test_follow_up_inherits_previous_kind() -> None:
    assert is_follow_up("and how much do they cost")
    assert part_kind("and how much do they cost", "what lamps does my car use") == "bulb"
    assert part_kind("what oil does it take", "what lamps does my car use") == "engine oil"
    assert "bulb" in topic_terms("bulb")


def test_fluid_rows_keep_whole_numbers_and_labels() -> None:
    retrieved = {"chunks": [{"page_number": 792, "content": DPF_CHUNK}, {"page_number": 792, "content": EUROPE_CHUNK}]}
    rows = fluid_rows(retrieved)
    values = {r["value"] for r in rows}
    assert "6.3 l (6.66 US qt.)" in values
    assert not any(v.startswith("3 l") for v in values)
    diesel = [r for r in rows if r["fuel"] == "diesel"]
    assert diesel and "6.3" in diesel[0]["numbers"]
    assert diesel[0]["kind"] == "engine oil"
    gasoline = [r for r in rows if r["fuel"] == "gasoline"]
    assert gasoline and "4.8" in gasoline[0]["numbers"]


def test_fuel_conflicts_catch_wrong_row() -> None:
    retrieved = {"chunks": [{"page_number": 792, "content": DPF_CHUNK}, {"page_number": 792, "content": EUROPE_CHUNK}]}
    rows = fluid_rows(retrieved)
    assert fuel_conflicts("Your car needs 4.8 litres (5.07 US qt.)", rows, "diesel") == ["4.8"]
    assert fuel_conflicts("Your car needs 6.3 l (6.66 US qt.)", rows, "diesel") == []
    assert fuel_conflicts("Your car needs 6.3 l", rows, "gasoline") == ["6.3"]
    assert fuel_conflicts("Your car needs 6.3 l", rows, None) == []
    assert rows_need_fuel(rows, None)
    assert not rows_need_fuel(rows, "diesel")


def test_grade_conflicts_block_petrol_footnote_on_diesel() -> None:
    draft = "requires API Service SM (or SL) / ACEA A5 (or A3), capacity 6.3 litres (6.66 US qt.)"
    assert grade_conflicts(draft, "diesel") == ["ACEA A5", "API Service SM"]
    assert grade_conflicts(draft, "gasoline") == []
    assert grade_conflicts("use ACEA C3 5W-30", "gasoline") == ["ACEA C3"]
    assert grade_conflicts("use ACEA C3 5W-30", None) == []
    rows = fluid_rows({"chunks": [{"page_number": 792, "content": DPF_CHUNK}, {"page_number": 792, "content": EUROPE_CHUNK}]})
    assert answer_conflicts("4.8 litres of API SM oil", rows, "diesel") == ["4.8 l", "API SM"]
    assert answer_conflicts("6.3 litres; the pages print no diesel grade", rows, "diesel") == []


def test_strip_wrong_fuel_grades_keeps_capacity() -> None:
    draft = (
        "Your 2.0 CRDi diesel takes **6.3 litres (6.66 US qt.)** with DPF. "
        "Do not use the gasoline oil spec (API SM / ILSAC GF-4) for your diesel. "
        "Refer to the viscosity chart. (page 792)"
    )
    cleaned = strip_wrong_fuel_grades(draft, "diesel")
    assert "6.3 litres" in cleaned
    assert "API SM" not in cleaned and "ILSAC" not in cleaned
    assert "viscosity chart" in cleaned
    assert "do not print an oil grade for the diesel engine" in cleaned
    assert strip_wrong_fuel_grades("6.3 litres, no grade printed.", "diesel") == "6.3 litres, no grade printed."


def test_footnote_paragraph_is_not_a_row_label() -> None:
    footnote = (
        "*5 If the API service SM or ACEA A5 engine oil is not available in your country, you are able "
        "to use API service SL or ACEA A3.\n*6 Diesel Particulate Filter\n0.58 l (0.61 US qt.)\n"
        "HYPOID GEAR OIL API GL-5, SAE 75W/90"
    )
    rows = fluid_rows({"chunks": [{"page_number": 793, "content": footnote}]})
    assert rows and rows[0]["kind"] == "axle oil"
    assert rows[0]["fuel"] is None


def test_atf_row_is_not_engine_oil() -> None:
    rows = fluid_rows({"chunks": [{"page_number": 792, "content": ATF_CHUNK}]})
    assert rows and rows[0]["kind"] == "ATF"
