"""Model-driven question understanding.

One structured call decides what the user is asking about, which car they mean (from
the message and the recent turns, unless a VIN already pinned it), what is still
missing before the manual can be quoted accurately, and the single question to ask
back if so. No keyword lists: the model reads the words. The regex heuristics in
app.intent / app.clarify remain only as the offline fallback when no API key is set.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger("hoodwise.understand")

# The categories downstream code can act on (retrieval boosts, spec extraction,
# shop queries). This is the app's taxonomy, not a guess at the user's wording.
PartKind = Literal[
    "engine oil", "coolant", "ATF", "brake fluid", "tire", "bulb", "battery",
    "fuse", "wiper blade", "filter", "spark plug", "belt", "brake pad", "other", "",
]
Intent = Literal["capacity", "type", "spec", "procedure", "definition", "purchase", "history", "other"]
Missing = Literal["vehicle", "year", "engine", "fuel", "part", "position"]


class VehicleMention(BaseModel):
    make: str | None = Field(None, description="Exactly as spelled in the catalog, or null")
    model: str | None = Field(None, description="Exactly as spelled in the catalog, or null")
    year: int | None = None
    displacement_l: str | None = Field(None, description='Engine size in litres like "2.0", or null')
    fuel: Literal["diesel", "gasoline"] | None = None


class Understanding(BaseModel):
    restated: str = Field(description="One plain sentence: what the user wants to know")
    part: PartKind = Field(description='The part or fluid the question is about; "" if none')
    intent: Intent
    vehicle: VehicleMention = Field(description="Car details stated by the user in this or earlier turns")
    is_reply_to_previous: bool = Field(description="True if this message answers our last question or continues the previous topic")
    needs_vehicle: bool = Field(description="True if the answer depends on which car it is")
    missing: list[Missing] = Field(description="What still blocks an accurate answer, most blocking first. Empty if nothing.")
    ask: str | None = Field(None, description="The single question to ask the user for missing[0], in their language. Null if missing is empty.")
    options: list[str] = Field(default_factory=list, description="Tap-to-answer options for `ask` (2-8 short labels). Empty if free text is better.")
    search_query: str = Field(description="Clean retrieval query for the manual: fixes typos, names the part, drops filler")


SYSTEM = """You are the understanding step of Hoodwise, a car assistant that answers only from
factory manuals. You do not answer the question. You decide what is being asked, which
car it is about, and what must be asked back before the manual can be quoted accurately.

You receive: the catalog of manuals we hold (make, model, model-year ranges), the car pinned
by VIN if any, the user's recent turns, and the new message.

Rules:
- Read the message in any language and with typos. Map the topic onto one of the part kinds.
- The car comes from the VIN when pinned. Otherwise read make/model/year/engine/fuel from the
  message and earlier turns, but only makes and models present in the catalog. A statement
  like "the model is hyundai santa fe 2016" is an answer to our previous question: merge it.
- Ask only when the answer would otherwise be a guess. Definitions and general explanations
  need no car. Specs, capacities, part types, procedures, and shopping need the car.
- If the model matches several model-year ranges in the catalog and no year is known, ask the
  year and offer the ranges as options.
- Fluid questions (oil, coolant, transmission fluid) differ by engine and by fuel. If fuel is
  unknown after VIN and text, ask petrol or diesel (options ["Petrol", "Diesel"]). If the
  engine size would change the row and is unknown, include it in the same question.
- Bulb questions differ by lamp position; if no position is named, ask which lamp.
- If the message is too vague to know the part ("what type", "how much"), ask which part.
- One question at a time, short, friendly, in the user's language. Never ask for something
  already known. Never ask for the VIN alone: offer VIN or make/model/year.
- search_query is for a full-text search over the manual: correct spelling, include the part
  and the measurement words (capacity, litres, type, torque), exclude the car name.
"""


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _catalog_text(catalog: list[dict[str, Any]]) -> str:
    lines = []
    for row in catalog:
        years = ", ".join(f"{a}-{b}" for a, b in row.get("years", []))
        lines.append(f"- {row['make']} {row['model']}: {years or 'years unknown'}")
    return "\n".join(lines) or "- (no manuals ingested)"


def _vehicle_text(vehicle: dict[str, Any] | None) -> str:
    if not vehicle:
        return "none"
    keep = {k: vehicle.get(k) for k in ("year", "make", "model", "engine_label", "displacement_l", "fuel", "fuel_source", "source")}
    return json.dumps({k: v for k, v in keep.items() if v})


def understand(
    message: str,
    context: str,
    pinned_vehicle: dict[str, Any] | None,
    catalog: list[dict[str, Any]],
) -> Understanding | None:
    """Returns None when no model is configured or the call fails; callers fall back."""
    if not settings.anthropic_api_key:
        return None
    user = (
        f"CATALOG OF MANUALS:\n{_catalog_text(catalog)}\n\n"
        f"CAR PINNED BY VIN: {_vehicle_text(pinned_vehicle)}\n\n"
        f"RECENT USER TURNS (oldest first): {context or '(none)'}\n\n"
        f"NEW MESSAGE: {message}"
    )
    import anthropic

    for model in (settings.understand_model, settings.chat_model):
        try:
            response = _client().messages.parse(
                model=model,
                max_tokens=1024,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=Understanding,
                output_config={"effort": "low"},
            )
        except anthropic.NotFoundError:
            log.warning("understand model %s not available, trying next", model)
            continue
        except anthropic.BadRequestError as exc:
            # Older chat models reject effort/thinking parameters; retry plain.
            log.warning("understand %s rejected request (%s); retrying without output_config", model, exc.message)
            try:
                response = _client().messages.parse(
                    model=model,
                    max_tokens=1024,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": user}],
                    output_format=Understanding,
                )
            except Exception as exc2:  # noqa: BLE001
                log.warning("understand failed on %s: %s", model, exc2)
                continue
        except Exception as exc:  # noqa: BLE001
            log.warning("understand failed on %s: %s", model, exc)
            continue
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return None
        parsed = response.parsed_output
        if parsed.missing and not parsed.ask:
            parsed.missing = []
        return parsed
    return None


def mention_dict(u: Understanding | None) -> dict[str, Any] | None:
    if not u or not (u.vehicle.make and u.vehicle.model):
        return None
    return {
        "make": u.vehicle.make,
        "model": u.vehicle.model,
        "year": u.vehicle.year,
        "displacement_l": u.vehicle.displacement_l,
        "fuel": u.vehicle.fuel,
    }
