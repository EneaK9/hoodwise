from app.clarify import clarification, needs_vehicle, vague
from app.intent import parse_vehicle_details, part_kind
from app.resolve import decoded_from_text

CATALOG = [("Honda", "Civic"), ("Hyundai", "Santa Fe"), ("Hyundai", "Tucson")]
SANTA_FE_RANGES = [(2000, 2006), (2007, 2011), (2016, 2018), (2019, 2023), (2024, 2026)]


def _ranges(make: str, model: str):
    return SANTA_FE_RANGES if model == "Santa Fe" else [(2015, 2020)]


def test_vague_question_asks_for_the_part() -> None:
    ask = clarification("what type", None, part_kind("what type"))
    assert ask and ask["missing"] == "part"
    assert "Engine oil" in ask["options"]
    assert vague("how much", "", "")
    # A short reply after a real question is not vague.
    assert not vague("and the rear ones", "", "what headlight bulb does my santa fe use")


def test_spec_question_without_a_car_asks_for_the_car() -> None:
    q = "how many liters of oil for the engine"
    ask = clarification(q, None, part_kind(q))
    assert ask and ask["missing"] == "vehicle"
    assert "VIN" in ask["ask"]
    # Definitions do not need a car.
    assert not needs_vehicle("what does DPF mean", "")
    assert clarification("what does DPF mean", None, "") is None


def test_text_car_without_year_asks_year_with_our_generations() -> None:
    q = "how many liters of oil for my hyundai santa fe diesel"
    vehicle = {**decoded_from_text(q, CATALOG), "label": "Hyundai Santa Fe"}
    ask = clarification(q, vehicle, part_kind(q), year_ranges=_ranges)
    assert ask and ask["missing"] == "year"
    assert "2016–2018" in ask["options"]


def test_fluid_question_without_fuel_asks_petrol_or_diesel() -> None:
    q = "how many liters of oil for my hyundai santa fe 2016 2.0"
    vehicle = {**decoded_from_text(q, CATALOG), "label": "2016 Hyundai Santa Fe 2.0L"}
    ask = clarification(q, vehicle, part_kind(q), year_ranges=_ranges)
    assert ask and ask["missing"] == "fuel"
    assert ask["options"] == ["Petrol", "Diesel"]


def test_complete_question_is_not_interrupted() -> None:
    q = "how many liters of oil for my hyundai santa fe 2016 2.0 diesel"
    vehicle = {**decoded_from_text(q, CATALOG), "label": "2016 Hyundai Santa Fe 2.0L diesel"}
    assert clarification(q, vehicle, part_kind(q), year_ranges=_ranges) is None


def test_bulb_question_asks_position_once() -> None:
    vehicle = {"source": "vin", "make": "Hyundai", "model": "Santa Fe", "year": 2016, "fuel": "diesel"}
    ask = clarification("what bulbs does my car use", vehicle, "bulb")
    assert ask and ask["missing"] == "position"
    assert clarification("what fog lamp bulb does my car use", vehicle, "bulb") is None
    assert clarification("all of them", vehicle, "bulb", context="what bulbs does my car use") is None


def test_statement_replies_count_as_answers_to_our_question() -> None:
    from app.intent import is_follow_up

    first = "how much oil should i put in my car"
    reply = "the model is hyundai santa fe 2016"
    assert is_follow_up(reply)
    assert is_follow_up("it's a 2.4 petrol")
    assert not is_follow_up("what lamps does my car use")
    assert not is_follow_up("how many liters of oil for my hyundai santa fe")
    assert part_kind(reply, first) == "engine oil"
    vehicle = {**decoded_from_text(reply, CATALOG, first), "label": "2016 Hyundai Santa Fe"}
    ask = clarification(reply, vehicle, part_kind(reply, first), context=first, year_ranges=_ranges)
    assert ask and ask["missing"] == "fuel"
    assert "Which engine" in ask["ask"]


def test_clarification_replies_merge_into_the_car() -> None:
    context = "how many liters of oil for my hyundai santa fe | 2016–2018"
    merged = decoded_from_text("Diesel", CATALOG, context)
    assert merged is not None
    assert merged["model"] == "Santa Fe"
    assert merged["year"] == 2016
    assert merged["fuel"] == "diesel"
    assert parse_vehicle_details("2.0 petrol") == {"year": None, "displacement_l": "2.0", "fuel": "gasoline"}
