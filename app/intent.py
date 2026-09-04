"""Question understanding shared by retrieval, answering, and shop links.

One place decides what part a question is about, so the shop query, the
retrieval boost, and the answer rules cannot disagree with each other.
"""

from __future__ import annotations

import re

# Ordered: the first matching kind wins. Specific phrases before generic words.
KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("tire", re.compile(r"\b(tires?|tyres?|wheel size)\b", re.I)),
    ("ATF", re.compile(r"\b(atf|transmission (?:fluid|oil)|gearbox oil|transaxle fluid)\b", re.I)),
    ("coolant", re.compile(r"\b(coolant|antifreeze|radiator fluid)\b", re.I)),
    ("brake fluid", re.compile(r"\bbrake fluid\b", re.I)),
    ("engine oil", re.compile(r"\b(engine oil|motor oil|oil|lubricant)\b", re.I)),
    ("battery", re.compile(r"\bbatter(?:y|ies)\b", re.I)),
    ("fuse", re.compile(r"\bfuses?\b", re.I)),
    ("wiper blade", re.compile(r"\bwipers?\b", re.I)),
    ("filter", re.compile(r"\bfilters?\b", re.I)),
    (
        "bulb",
        re.compile(
            r"light\s*bul[bd]s?|headlights?|headlamps?|\bbul[bd]s?\b|\blamps?\b|\bfog\b|"
            r"\blights?\b|\bindicators?\b|\bturn signals?\b|\bbrake lights?\b|\bdrl\b",
            re.I,
        ),
    ),
]

# Words that carry the retrieval for each kind. Used to keep a bulb question on
# lighting pages instead of drifting to whatever table matched "type" or "need".
TOPIC_TERMS: dict[str, list[str]] = {
    "tire": ["tire", "tyre", "pressure", "kPa", "psi"],
    "ATF": ["transmission fluid", "ATF", "transaxle"],
    "coolant": ["coolant", "antifreeze", "ethylene"],
    "brake fluid": ["brake fluid", "DOT"],
    "engine oil": ["engine oil", "viscosity", "API", "ACEA", "ILSAC", "lubricant"],
    "battery": ["battery", "Ah", "CCA"],
    "fuse": ["fuse", "amp", "relay"],
    "wiper blade": ["wiper", "blade"],
    "filter": ["filter", "cartridge"],
    "bulb": ["bulb", "wattage", "headlamp", "headlight", "fog lamp", "lamp", "W"],
}

FLUID_KINDS = {"engine oil", "coolant", "ATF", "brake fluid"}

FOLLOW_UP = re.compile(
    r"^\s*(and|also|what about|how about|same for|then|ok|okay|now)\b|"
    r"^\s*(it|that|this|them|those|these)\b",
    re.I,
)

_TYPO_FIXES = (
    (re.compile(r"lightbuld", re.I), "lightbulb"),
    (re.compile(r"\bbuld\b", re.I), "bulb"),
    (re.compile(r"\btyers?\b", re.I), "tyre"),
    (re.compile(r"\bcoolent\b", re.I), "coolant"),
)


def normalize(question: str) -> str:
    text = question or ""
    for pattern, fix in _TYPO_FIXES:
        text = pattern.sub(fix, text)
    return text


def part_kind(question: str, context: str = "") -> str:
    """Part category for a question. Falls back to the previous turn on follow-ups."""
    text = normalize(question)
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(text):
            return kind
    if context and is_follow_up(text):
        return part_kind(context)
    return ""


_QUESTION_WORDS = re.compile(
    r"\b(what|how|which|where|why|when|does|do|can|could|should|need|is there|are there|tell me|show)\b", re.I
)
_DETAIL_FILLER = {
    "the", "a", "an", "my", "car", "model", "make", "year", "engine", "is", "its", "it's", "it",
    "i", "have", "drive", "own", "got", "one", "mine", "version", "with", "and", "of", "for",
    "petrol", "gasoline", "gas", "diesel", "hybrid", "electric", "liter", "litre", "liters", "litres",
    "l", "cc", "turbo", "crdi", "gdi", "mpi", "tdi", "manual", "automatic", "auto", "yes", "no",
    "please", "thanks", "ok", "okay", "sure", "actually",
}


def _detail_reply(text: str) -> bool:
    """"the model is hyundai santa fe 2016", "2.0 diesel", "it's a 2.4 petrol": a statement
    of car details with no question in it. That is an answer to what we asked last turn."""
    if _QUESTION_WORDS.search(text or ""):
        return False
    words = [w for w in re.findall(r"[a-z0-9.']+", (text or "").lower())]
    if not words:
        return False
    rest = [w for w in words if w not in _DETAIL_FILLER and not re.fullmatch(r"\d+(?:\.\d+)?", w)]
    return len(rest) <= 3


def is_follow_up(question: str) -> bool:
    words = re.findall(r"[A-Za-z0-9']+", question or "")
    if not words:
        return False
    if FOLLOW_UP.search(question or ""):
        return True
    return len(words) <= 4 or _detail_reply(question)


def topic_terms(kind: str) -> list[str]:
    return list(TOPIC_TERMS.get(kind, []))


_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_DISPLACEMENT = re.compile(r"\b(\d\.\d)\s*(?:-?\s*(?:l|liter|litre|litter)s?\b|\s*(?:crdi|tdi|gdi|t-gdi|mpi))?", re.I)
_MODEL_ALIASES = {
    "santafe": "Santa Fe",
    "santa-fe": "Santa Fe",
    "ioniq5": "Ioniq 5",
    "ioniq6": "Ioniq 6",
    "ioniq9": "Ioniq 9",
}


def parse_vehicle_details(text: str) -> dict[str, object]:
    """Year, engine size, fuel from a short reply like "2016", "2.0 diesel", "Petrol"."""
    from app.vin import _fuel_from_text

    year_m = _YEAR.search(text or "")
    range_m = re.search(r"\b((?:19|20)\d{2})\s*[–-]\s*(?:19|20)?\d{2}\b", text or "")
    disp_m = _DISPLACEMENT.search(text or "")
    return {
        "year": int(range_m.group(1)) if range_m else (int(year_m.group(1)) if year_m else None),
        "displacement_l": disp_m.group(1) if disp_m else None,
        "fuel": _fuel_from_text(text),
    }


def parse_vehicle_mention(text: str, catalog: list[tuple[str, str]]) -> dict[str, object] | None:
    """Read make, model, year, engine size and fuel out of a question like
    "oil for my hyundai santa fe 2016 2.0 liter diesel". `catalog` is the list of
    (make, model) pairs we have manuals for; nothing outside it is guessed."""
    from app.vin import _fuel_from_text

    if not text or not catalog:
        return None
    low = f" {re.sub(r'[^a-z0-9. -]+', ' ', text.lower())} "
    for alias, canonical in _MODEL_ALIASES.items():
        low = low.replace(f" {alias} ", f" {canonical.lower()} ")
    makes = {m.lower(): m for m, _ in catalog}
    make = next((makes[m] for m in makes if f" {m} " in low), None)
    models = [(mk, md) for mk, md in catalog if not make or mk == make]
    best: tuple[str, str] | None = None
    for mk, md in models:
        if f" {md.lower()} " in low and (best is None or len(md) > len(best[1])):
            best = (mk, md)
    if not best:
        return None
    make, model = best
    return {"make": make, "model": model, **parse_vehicle_details(text)}
