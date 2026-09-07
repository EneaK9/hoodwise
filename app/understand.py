"""Model-driven question understanding.

One structured call decides what the user is asking about, which car they mean (from
the VIN decode when pinned, otherwise from the message and recent turns), the engine
and fuel, what is still missing before the manual can be quoted accurately, and the
single question to ask back if so. There are no keyword lists anywhere in this path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger("hoodwise.understand")

Missing = Literal["vehicle", "year", "engine", "fuel", "part", "position", "other"]


class VehicleRead(BaseModel):
    make: str | None = Field(None, description="Exactly as spelled in the catalog, or null")
    model: str | None = Field(None, description="Exactly as spelled in the catalog, or null")
    year: int | None = None
    displacement_l: str | None = Field(None, description='Engine size in litres like "2.0", or null')
    engine: str | None = Field(None, description='Engine family if known, e.g. "2.0 CRDi", "1.5 VTEC Turbo"')
    fuel: Literal["diesel", "gasoline", "hybrid", "electric"] | None = None
    fuel_basis: str | None = Field(None, description="One short phrase: where the fuel came from (user said, decoder field, engine size for this make, CO2 vs consumption)")


class Understanding(BaseModel):
    restated: str = Field(description="One plain sentence: what the user wants to know")
    manual_id: str | None = Field(None, description="The id of the catalog entry (manual edition) that matches the car's make, model year and powertrain; null if the car is unknown or we hold no manual for it")
    part: str = Field(description='The part, fluid, or system in 1-3 words, lowercase, in English (e.g. "engine oil", "headlight bulb", "wheel nut torque"); "" if none')
    needs_vehicle: bool = Field(description="True if an accurate answer depends on which car and engine it is")
    vehicle: VehicleRead = Field(description="The car: from the VIN decode when pinned, else from the user's words across turns")
    is_reply_to_previous: bool = Field(description="True if this message answers our last question or continues the previous topic")
    missing: list[Missing] = Field(description="What still blocks an accurate answer, most blocking first. Empty if nothing.")
    ask: str | None = Field(None, description="The single question to ask the user for missing[0], in the user's language. Null if missing is empty.")
    options: list[str] = Field(default_factory=list, description="Tap-to-answer options for `ask` (2-8 short labels). Empty if free text is better.")
    search_query: str = Field(description="Full-text search phrase for the manual: correct spelling, the part, the measurement words; no car name")
    web_query: str = Field(description="A web search phrase for cross-checking: include make, model, year, engine and the part")


SYSTEM = """You are the understanding step of Hoodwise, a car assistant that answers from factory
manuals and cross-checks the web. You do not answer the question. You decide what is being
asked, which car and engine it is about, and what must be asked back before an accurate answer
is possible.

You receive: the catalog of manuals we hold (make, model, model-year ranges), the car pinned by
VIN if any (with every field the decoder returned), the user's recent turns, and the new message.

How to work:
- Read the message in any language, with typos. Name the part in plain English.
- The car: when a VIN is pinned, use it and fill engine and fuel from its data. Displacement in cc,
  a fuel field, the engine code, and CO2 vs fuel consumption all tell you the engine. State the
  basis. When no VIN is pinned, read make, model, year, engine and fuel from the message and the
  earlier turns, using only makes and models present in the catalog. A statement such as
  "the model is hyundai santa fe 2016" or "2.0 diesel" answers our previous question: merge it.
- Ask only when the answer would otherwise be a guess. Definitions and general explanations need
  no car. Specs, capacities, part types, procedures and shopping need the car, and fluids need the
  engine and fuel because the rows differ. Lamps need the position. If the message is too vague
  to know the part, ask which part.
- Pick manual_id: the catalog entry whose make, model and year range contain the car and whose
  edition matches its powertrain (a petrol or diesel car is not the Hybrid, PHEV or Electric
  edition; "Owner manual" is the conventional-engine edition). If the year is unknown and several
  ranges fit, ask the year and offer the ranges as options. If the powertrain edition is ambiguous
  and the answer depends on it, ask.
- One question at a time, short, friendly, in the user's language. Never ask for what is already
  known. Never demand the VIN alone: offer VIN or make, model and year.
- search_query is for full-text search over the manual: the part plus measurement words
  (capacity, litres, type, torque, pressure), no car name. web_query names the car and the part.
"""


def _catalog_text(catalog: list[dict[str, Any]]) -> str:
    lines = []
    for row in catalog:
        years = f"{row.get('year_from')}-{row.get('year_to')}" if row.get("year_from") else "years unknown"
        edition = f" [{row['edition']}]" if row.get("edition") else ""
        lines.append(f"- id={row['id']} {row['make']} {row['model']} {years}{edition}")
    return "\n".join(lines) or "- (no manuals ingested)"


def _vehicle_text(vehicle: dict[str, Any] | None) -> str:
    if not vehicle:
        return "none"
    keep = {
        k: vehicle.get(k)
        for k in (
            "year", "make", "model", "trim", "body", "engine_label", "engine_code", "displacement_l",
            "displacement_cc", "fuel", "fuel_raw", "fuel_source", "emissions_hint", "transmission", "source", "specs",
        )
    }
    return json.dumps({k: v for k, v in keep.items() if v}, ensure_ascii=False)


def understand(
    message: str,
    context: str,
    pinned_vehicle: dict[str, Any] | None,
    catalog: list[dict[str, Any]],
) -> Understanding | None:
    """Returns None when the call fails; the caller then asks the user for the basics."""
    import anthropic

    user = (
        f"CATALOG OF MANUALS:\n{_catalog_text(catalog)}\n\n"
        f"CAR PINNED BY VIN: {_vehicle_text(pinned_vehicle)}\n\n"
        f"RECENT USER TURNS (oldest first): {context or '(none)'}\n\n"
        f"NEW MESSAGE: {message}"
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    for model in dict.fromkeys((settings.understand_model, settings.chat_model)):
        try:
            response = client.messages.parse(
                model=model,
                max_tokens=1500,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=Understanding,
                output_config={"effort": "low"},
            )
        except anthropic.NotFoundError:
            log.warning("understand: model %s not available, trying next", model)
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
        "engine": u.vehicle.engine,
        "fuel": u.vehicle.fuel,
        "fuel_basis": u.vehicle.fuel_basis,
        "manual_id": u.manual_id,
    }
