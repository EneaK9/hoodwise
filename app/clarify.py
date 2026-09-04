"""Ask before answering when the question cannot be answered accurately yet.

Runs after the car and the part category have been resolved and before retrieval.
One question at a time, the most blocking first: which car, which year, which engine
or fuel, which part. Every question carries tap-to-answer options.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.intent import FLUID_KINDS, is_follow_up, part_kind
from app.retrieval import is_numeric_query

STOP = {
    "what", "is", "the", "a", "an", "for", "on", "of", "to", "in", "and", "or", "how",
    "do", "does", "with", "from", "this", "that", "my", "your", "car", "much", "many",
    "please", "need", "want", "tell", "me", "it", "its", "about", "type", "kind", "which",
    "should", "use", "can", "i", "you", "we", "there", "are", "hi", "hello", "hey", "ok",
}
PART_OPTIONS = ["Engine oil", "Bulbs / lamps", "Tyres", "Coolant", "Transmission fluid", "Battery", "Brake fluid"]
GENERIC_ONLY = re.compile(
    r"\b(what does .* mean|what is (?:a|an|the) \w+|explain|difference between|warning light|"
    r"dashboard|symbol|indicator light|history|stolen|market value|price)\b",
    re.I,
)


def _meaningful_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9.]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def needs_vehicle(question: str, kind: str) -> bool:
    """Specs, capacities, and part types depend on the car. Definitions do not."""
    if GENERIC_ONLY.search(question or ""):
        return False
    return bool(kind) or is_numeric_query(question)


def vague(question: str, kind: str, context: str) -> bool:
    """Too little to act on: no part, no spec words, few content words, no usable prior turn."""
    if kind or is_numeric_query(question):
        return False
    if context and is_follow_up(question) and part_kind(context):
        return False
    return len(_meaningful_words(question)) < 3


def clarification(
    question: str,
    vehicle: dict[str, Any] | None,
    kind: str,
    context: str = "",
    year_ranges: Callable[[str, str], list[tuple[int, int]]] | None = None,
) -> dict[str, Any] | None:
    """Return {"ask", "missing", "options"} or None when we know enough to answer."""
    q = question or ""
    if GENERIC_ONLY.search(q):
        # "what does DPF mean" is short but complete.
        return None
    if vague(q, kind, context):
        return {
            "missing": "part",
            "ask": "What is the question about? Pick the part or fluid, or type it.",
            "options": PART_OPTIONS,
        }
    if not needs_vehicle(q, kind):
        return None
    if not vehicle:
        return {
            "missing": "vehicle",
            "ask": (
                f"Which car is this {kind or 'spec'} for? Paste the VIN with the car icon, "
                "or tell me the make, model, and year."
            ),
            "options": [],
        }
    from_text = vehicle.get("source") == "text"
    make, model = vehicle.get("make"), vehicle.get("model")
    if from_text and not vehicle.get("year") and make and model and year_ranges:
        ranges = year_ranges(str(make), str(model))
        if len(ranges) > 1:
            labels = [f"{a}–{b}" if a != b else str(a) for a, b in ranges]
            return {
                "missing": "year",
                "ask": f"Which year is your {make} {model}? The manuals differ by generation.",
                "options": labels,
            }
    if kind in FLUID_KINDS and not vehicle.get("fuel") and not vehicle.get("electric"):
        engine = vehicle.get("engine_label")
        if engine:
            ask = f"Is your {make} {model} {engine} petrol or diesel? The {kind} row differs by fuel."
        else:
            ask = (
                f"Which engine is in your {make} {model}? Petrol or diesel, and the size if you know it "
                f"(for example \"2.0 diesel\" or \"2.4 petrol\"). The {kind} row differs by engine."
            )
        return {"missing": "fuel", "ask": ask, "options": ["Petrol", "Diesel"]}
    if kind == "bulb" and not re.search(
        r"head|fog|brake|tail|reverse|indicator|turn|plate|interior|cabin|dome|drl|daytime|position|side|low beam|high beam|all",
        f"{q} {context}",
        re.I,
    ):
        return {
            "missing": "position",
            "ask": "Which lamp? Bulb types differ by position.",
            "options": ["Headlight low beam", "Headlight high beam", "Fog lamp", "Brake / tail", "Indicator", "Number plate", "Interior", "All of them"],
        }
    return None
