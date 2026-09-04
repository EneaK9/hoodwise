"""Dual-source spec extraction: text-layer regex + vision, with double-pass verify."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ingestion.extract import ExtractedDocument
from ingestion.models import ExtractedSpec, SpecExtractionResult
from ingestion.vision import extract_specs_from_image

TORQUE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*N[·.]?m\s*"
    r"(?:\((?P<kgf>\d+(?:\.\d+)?)\s*kgf[·.]?m,\s*(?P<lbf>\d+(?:\.\d+)?)\s*lbf[·.]?ft\))?",
    re.IGNORECASE,
)

REPLACE_RE = re.compile(r"\breplace\b", re.IGNORECASE)
CONDITION_RE = re.compile(
    r"(except\s+type-?r|type-?r|except\s+si|\bSi\b|1\.5\s*L|2\.0\s*L)",
    re.IGNORECASE,
)


def extract_specs_from_text(text: str, page_number: int) -> list[ExtractedSpec]:
    specs: list[ExtractedSpec] = []
    flat = re.sub(r"\s+", " ", text)
    for match in TORQUE_RE.finditer(flat):
        lead = flat[max(0, match.start() - 120) : match.start()].strip(" -:.")
        part = re.split(r"[.;]", lead)[-1].strip()[-80:] or "unspecified fastener"
        raw_clean = re.sub(r"\s+", " ", match.group(0)).strip()
        window = lead[-80:] + " " + raw_clean
        conds = list(CONDITION_RE.finditer(window))
        condition = conds[-1].group(0) if conds else None
        specs.append(
            ExtractedSpec(
                part_name=part[:200],
                spec_type="torque",
                value_raw=raw_clean,
                value_nm=float(match.group("value")),
                unit="N·m",
                replace_required=bool(REPLACE_RE.search(lead)),
                condition_note=condition,
                raw_context=lead[-240:],
                source="text",
                page_number=page_number,
                confidence=0.85,
            )
        )
    return specs


def _key(spec: ExtractedSpec) -> tuple:
    return (
        spec.page_number,
        spec.spec_type,
        spec.value_nm,
        (spec.condition_note or "").lower(),
    )


def merge_and_verify(
    text_specs: list[ExtractedSpec],
    vision_a: list[ExtractedSpec],
    vision_b: list[ExtractedSpec],
) -> list[tuple[ExtractedSpec, str]]:
    """Return (spec, verification_status). Disagreement between vision passes is flagged."""
    a_keys = {_key(s) for s in vision_a}
    b_keys = {_key(s) for s in vision_b}
    agreed_vision = [s for s in vision_a if _key(s) in b_keys]
    flagged_vision = [s for s in vision_a + vision_b if _key(s) not in (a_keys & b_keys)]

    merged: dict[tuple, tuple[ExtractedSpec, str]] = {}
    for spec in text_specs:
        merged[_key(spec)] = (spec, "auto_agreed")
    for spec in agreed_vision:
        key = _key(spec)
        if key in merged:
            existing, _ = merged[key]
            existing.source = "merged"
            existing.confidence = max(existing.confidence, spec.confidence)
        else:
            merged[key] = (spec, "auto_agreed")
    for spec in flagged_vision:
        key = _key(spec)
        if key not in merged:
            merged[key] = (spec, "flagged")
    return list(merged.values())


def extract_document_specs(doc: ExtractedDocument, use_vision: bool = True) -> list[tuple[ExtractedSpec, str]]:
    text_specs: list[ExtractedSpec] = []
    vision_a: list[ExtractedSpec] = []
    vision_b: list[ExtractedSpec] = []

    for page in doc.pages:
        text_specs.extend(extract_specs_from_text(page.text, page.page_number))
        if not use_vision:
            continue
        candidates = [
            im
            for im in page.images
            if not im.discarded and im.image_path and Path(im.image_path).exists()
        ]
        for im in candidates:
            first = extract_specs_from_image(Path(im.image_path), page.page_number)
            second = extract_specs_from_image(Path(im.image_path), page.page_number)
            vision_a.extend(first.specs)
            vision_b.extend(second.specs)

    return merge_and_verify(text_specs, vision_a, vision_b)


def dump_specs(path: Path, rows: list[tuple[ExtractedSpec, str]]) -> None:
    payload = [{"status": status, **spec.model_dump()} for spec, status in rows]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
