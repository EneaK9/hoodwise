from app.answer import numbers_are_grounded
from app.resolve import apply_model_read, decoded_from_mention, public_vehicle, vehicle_label
from app.retrieval import _terms, _ts_or_query


def test_vehicle_label_has_no_placeholders_and_no_duplicates() -> None:
    label = vehicle_label({"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0 CRDi", "fuel": "diesel"})
    assert label == "2016 Hyundai Santa Fe 2.0 CRDi diesel"
    assert vehicle_label({"make": "Hyundai"}) == "Hyundai (model unconfirmed)"


def test_text_vehicle_from_model_read() -> None:
    decoded = decoded_from_mention({"make": "Hyundai", "model": "Santa Fe", "year": 2016, "displacement_l": "2.0", "engine": "2.0 CRDi", "fuel": "diesel"})
    assert decoded is not None and decoded["source"] == "text"
    pub = public_vehicle(decoded)
    assert pub is not None
    assert pub["fuel"] == "diesel" and pub["fuel_source"] == "user"
    assert pub["label"] == "2016 Hyundai Santa Fe 2.0 CRDi diesel"
    assert "not a VIN" in pub["note"]
    assert decoded_from_mention({"year": 2016}) is None


def test_model_read_fills_only_what_the_vin_left_empty() -> None:
    vin_decode = {"make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0L", "fuel": None, "displacement_l": "2.0"}
    out = apply_model_read(dict(vin_decode), {"engine": "2.0 CRDi", "fuel": "diesel", "fuel_basis": "1995 cc on a Hyundai"})
    assert out["fuel"] == "diesel"
    assert out["fuel_source"].startswith("model:")
    assert out["engine_label"] == "2.0 CRDi"
    owner = {"make": "Hyundai", "model": "Santa Fe", "fuel": "gasoline", "fuel_source": "user"}
    assert apply_model_read(dict(owner), {"fuel": "diesel"})["fuel"] == "gasoline"


def test_unknown_fuel_asks_for_confirmation() -> None:
    pub = public_vehicle({"year": 2016, "make": "Hyundai", "model": "Santa Fe", "engine_label": "2.0L", "specs": []})
    assert pub is not None
    assert pub["fuel"] is None
    assert pub["needs_fuel_confirmation"] is True


def test_retrieval_uses_the_phrase_as_given() -> None:
    assert _terms("engine oil capacity litres") == ["engine", "oil", "capacity", "litres"]
    assert _ts_or_query("engine oil capacity") == "engine | oil | capacity"
    assert _ts_or_query("") == "specification"


def test_number_grounding_accepts_sources_question_and_web_only() -> None:
    retrieved = {"specs": [], "chunks": [{"content": "Diesel Engine with DPF 6.3 l (6.66 US qt.)", "page_number": 792, "doc_id": "HY"}]}
    ok, missing = numbers_are_grounded("Takes 6.3 litres (6.66 US qt.) (p.792)", retrieved)
    assert ok and missing == []
    ok, missing = numbers_are_grounded("Takes 6.7 litres", retrieved)
    assert not ok and missing == ["6.7"]
    ok, _ = numbers_are_grounded("Takes 6.7 litres", retrieved, web_passages=[{"cited_text": "capacity 6.7 litres"}])
    assert ok
    ok, _ = numbers_are_grounded("For your 2016 2.0 diesel: 6.3 l", retrieved, question="2016 2.0 diesel oil")
    assert ok
