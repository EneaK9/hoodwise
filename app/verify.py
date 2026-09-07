"""Model verification of a drafted answer against its sources.

The drafting model reads; a second call checks. It sees the pinned car, the manual pages
that were retrieved, the web passages the draft cited, and the draft. It reports every
number or specification that is not supported by a source for this exact car and fuel.
Code keeps one deterministic backstop next to it: every digit string in the answer must
appear verbatim in a source (see app.answer.numbers_are_grounded).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger("hoodwise.verify")


class Verdict(BaseModel):
    ok: bool = Field(description="True only if every claim the draft attributes to the MANUAL is supported by the pages for this exact car, engine and fuel, and no figure is invented")
    manual_problems: list[str] = Field(default_factory=list, description="Claims attributed to the manual (or stated as fact without a web attribution) that the pages do not support for this car, engine and fuel. Quote the claim and what the page actually says")
    web_problems: list[str] = Field(default_factory=list, description="Claims attributed to a web source whose cited passage does not literally contain them, or that contradict the manual without saying so. Quote the claim and the passage")
    shop_item: str | None = Field(None, description='Search-ready name of the purchasable item the answer names, e.g. "5W-30 ACEA C3 diesel engine oil" or "H7 headlight bulb". Null if none')
    confidence: float = Field(description="0 to 1, how sure you are of the verdict")

    @property
    def problems(self) -> list[str]:
        return list(self.manual_problems) + list(self.web_problems)


SYSTEM = """You verify answers for Hoodwise, a car assistant. You receive the pinned car, the manual
pages that were retrieved, the web passages the draft cited, and the draft answer.

Check every number, unit, grade, code and part name in the draft, and sort each problem into one of
two lists:

MANUAL problems (these block the answer):
- A claim presented as the manual's, or as plain fact, that the pages do not print for this car's
  engine and fuel. A figure printed for another engine size or the other fuel is wrong even if it is
  on the same page. Footnotes and rows belong to the row they are attached to.
- A conversion the draft made (US quarts to litres, kgf·m to N·m) whose original is not printed or
  whose arithmetic is off.
- A statement that the manual does not print something, when the pages given do print it for this car.

WEB problems (these are reported, not blocking):
- A claim attributed to a named web source whose cited passage does not literally contain it (the
  passages are excerpts; say so), or that contradicts the manual without the draft saying so.

Quote each problem exactly and say what the source actually says. Be strict on manual claims: an
unsupported figure is a failure even if it is plausible.
"""


def verify(
    draft: str,
    vehicle: dict[str, Any] | None,
    manual_pages: list[dict[str, Any]],
    web_passages: list[dict[str, Any]],
    specs: list[dict[str, Any]] | None = None,
    manual: dict[str, Any] | None = None,
) -> Verdict | None:
    import anthropic

    pages = "\n\n".join(
        f"[{p.get('doc_id')} p.{p.get('page_number')}]\n{(p.get('content') or '')[:6000]}" for p in manual_pages
    ) or "(none)"
    spec_rows = "\n".join(
        json.dumps({
            "part": s.get("part_name"), "value": s.get("value_raw"), "condition": s.get("condition_note"),
            "context": (s.get("raw_context") or "")[:300], "doc": s.get("doc_id"), "page": s.get("page_number"),
        }, ensure_ascii=False)
        for s in (specs or [])
    ) or "(none)"
    manual_line = (
        f"{manual.get('make')} {manual.get('model')} {manual.get('year_from')}-{manual.get('year_to')} ({manual.get('titles')}); docs: {manual.get('doc_ids')}"
        if manual else "(unknown)"
    )
    web = "\n\n".join(
        f"[{w.get('title') or w.get('url')}] {w.get('url')}\n"
        + (w.get("cited_text") or "(page consulted by the draft; no passage quoted)")
        for w in web_passages
    ) or "(none)"
    keep = {k: (vehicle or {}).get(k) for k in ("year", "make", "model", "engine_label", "displacement_l", "fuel", "fuel_source", "source")}
    user = (
        f"PINNED CAR: {json.dumps({k: v for k, v in keep.items() if v}, ensure_ascii=False) or 'none'}\n\n"
        f"MANUAL USED: {manual_line}\n\n"
        f"SPEC ROWS EXTRACTED FROM THIS MANUAL'S TABLES (these are manual figures, with page and condition):\n{spec_rows}\n\n"
        f"MANUAL PAGES RETRIEVED:\n{pages}\n\nWEB PASSAGES CITED BY THE DRAFT:\n{web}\n\nDRAFT ANSWER:\n{draft}"
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    for model in dict.fromkeys((settings.verify_model, settings.chat_model)):
        try:
            response = client.messages.parse(
                model=model,
                max_tokens=2000,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=Verdict,
                output_config={"effort": "medium"},
            )
        except anthropic.NotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("verify failed on %s: %s", model, exc)
            continue
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return None
        return response.parsed_output
    return None
