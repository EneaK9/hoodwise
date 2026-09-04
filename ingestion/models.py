from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VisionClassification(BaseModel):
    classification: Literal["diagram", "spec_callout", "table", "decorative", "unknown"]
    caption: str = ""
    contains_numeric_specs: bool = False


class ExtractedSpec(BaseModel):
    part_name: str
    spec_type: str = Field(description="torque, capacity, clearance, angle, pressure, other")
    value_raw: str
    value_nm: float | None = None
    unit: str | None = None
    torque_sequence: str | None = None
    replace_required: bool = False
    condition_note: str | None = None
    raw_context: str = ""
    source: Literal["text", "vision"] = "text"
    page_number: int
    confidence: float = 0.7


class SpecExtractionResult(BaseModel):
    specs: list[ExtractedSpec] = Field(default_factory=list)
