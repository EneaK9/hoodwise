"""Draft an answer from the retrieved manual pages plus web search, then verify it.

Flow: the drafting model receives the pinned car, the whole manual pages retrieved for
the understanding model's search phrase, and Claude's web search tool for what the manual
does not print. A second model call (app.verify) checks every figure against the sources.
One deterministic backstop stays: every digit string in the answer must appear verbatim
in a source. On failure the draft is redone once with the objections, then refused.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.verify import verify

log = logging.getLogger("hoodwise.answer")

DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")

SYSTEM = """You are Hoodwise, an assistant that answers car questions from the factory manual first
and the web second. You are given the pinned car, the manual pages retrieved for the question,
and a web search tool.

Rules:
- Quote the manual for this exact car, engine and fuel. A table row for another engine size or the
  other fuel is not this car's figure, even on the same page. Footnotes belong to the row they are
  attached to. If the pages print no row for this car's engine or fuel, say so plainly.
- Cite every manual figure with its page like "(p.792)". Do not invent numbers, grades or codes.
  Quote units exactly as printed; you may add a conversion only when the source prints the original.
- Use web search when the manual does not print what was asked (an oil grade, a bulb code, a part
  number, a torque the owner handbook lacks) or when the user asks to compare. Attribute anything
  from the web to the web with its source, and keep it separate from manual figures. A web fact may
  only be stated if the passage you cite for it literally contains it: cite the sentence that holds
  the figure or grade, and do not add anything the cited passages do not say, even if you read it on
  the page. If the web and the manual disagree, show both and say which is the factory figure.
- No car pinned: name the manual (make, model, years) each figure comes from, and ask for the car
  if the pages mix vehicles.
- Answer in the language the question is written in, briefly. Say which manual and page you used.
  Say when the source is an owner handbook rather than a workshop manual.
"""


def numbers_are_grounded(
    answer: str,
    retrieved: dict[str, Any],
    vehicle: dict[str, Any] | None = None,
    question: str = "",
    web_passages: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    """Every digit string in the answer must appear in a source, the question, or the car.

    This is the one deterministic check kept on purpose. It has no vocabulary: it only
    asks whether the exact characters exist somewhere the model was allowed to read.
    """
    parts: list[str] = [question or ""]
    for spec in retrieved.get("specs") or []:
        parts.append(" ".join(str(spec.get(k) or "") for k in ("value_raw", "part_name", "condition_note", "raw_context", "page_number", "doc_id", "value_nm")))
    for chunk in retrieved.get("chunks") or []:
        parts.append(f"{chunk.get('content','')} {chunk.get('page_number','')} {chunk.get('doc_id','')}")
    for w in web_passages or []:
        parts.append(str(w.get("cited_text") or ""))
    if retrieved.get("manual"):
        # The manual's own identity (title, year range, doc ids) is quotable.
        parts.append(" ".join(str(v) for v in retrieved["manual"].values() if v))
    if vehicle:
        parts.append(" ".join(str(v) for v in vehicle.values() if v and not isinstance(v, (dict, list))))
    blob = "\n".join(parts)
    missing = []
    for num in DIGIT_RE.findall(answer or ""):
        if len(num) <= 1:
            continue
        if num not in blob:
            missing.append(num)
    return (len(missing) == 0, missing)


def _format_context(retrieved: dict[str, Any], vehicle: dict[str, Any] | None, context: str, restated: str) -> str:
    keep = {k: (vehicle or {}).get(k) for k in ("year", "make", "model", "trim", "engine_label", "displacement_l", "fuel", "fuel_source", "source", "note")}
    lines = [f"PINNED CAR: {json.dumps({k: v for k, v in keep.items() if v}, ensure_ascii=False) if vehicle else 'none'}"]
    if restated:
        lines.append(f"QUESTION UNDERSTOOD AS: {restated}")
    if context:
        lines.append(f"EARLIER USER TURNS: {context}")
    if retrieved.get("manual"):
        m = retrieved["manual"]
        lines.append(f"MANUAL USED: {m.get('make')} {m.get('model')} {m.get('year_from')}-{m.get('year_to')} ({m.get('titles')}); docs: {m.get('doc_ids')}")
    lines.append(
        "SPEC ROWS (values the ingestion pipeline extracted from this manual's torque and "
        "specification tables, each with its page and condition; they ARE manual figures — quote "
        "them with the page, and honour the condition against the pinned car):"
    )
    if not retrieved.get("specs"):
        lines.append("(none)")
    for spec in retrieved.get("specs") or []:
        lines.append(json.dumps({
            "part": spec.get("part_name"), "value": spec.get("value_raw"), "condition": spec.get("condition_note"),
            "context": (spec.get("raw_context") or "")[:300], "doc": spec.get("doc_id"), "page": spec.get("page_number"),
        }, ensure_ascii=False))
    lines.append("MANUAL PAGES (whole pages, table cells may be split across lines):")
    if not retrieved.get("chunks"):
        lines.append("(none)")
    for chunk in retrieved.get("chunks") or []:
        lines.append(f"--- {chunk.get('doc_id')} p.{chunk.get('page_number')} ({chunk.get('section_name') or chunk.get('section_path') or ''}) ---")
        lines.append((chunk.get("content") or "")[:8000])
    return "\n".join(lines)


def _web_passages(response: Any) -> list[dict[str, Any]]:
    """Web evidence the draft used: cited passages from the text blocks first (these carry
    the quoted text), then the pages the search returned, so the verifier at least knows
    which sources were consulted."""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for block in response.content:
        if getattr(block, "type", "") != "text":
            continue
        for cit in getattr(block, "citations", None) or []:
            url = getattr(cit, "url", None)
            if not url:
                continue
            key = (url, getattr(cit, "cited_text", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append({"url": url, "title": getattr(cit, "title", None), "cited_text": getattr(cit, "cited_text", "")})
    cited_urls = {p["url"] for p in out}
    for block in response.content:
        if getattr(block, "type", "") != "web_search_tool_result":
            continue
        results = getattr(block, "content", None)
        if not isinstance(results, list):
            continue  # an error object, not a result list
        for r in results:
            url = getattr(r, "url", None)
            if url and url not in cited_urls:
                cited_urls.add(url)
                out.append({"url": url, "title": getattr(r, "title", None), "cited_text": "", "consulted_only": True})
    return out


def _draft(client: Any, question: str, prompt_context: str, web_query: str, correction: str = "") -> tuple[str, list[dict[str, Any]], str]:
    import anthropic

    user = (
        f"Question:\n{question}\n\n{prompt_context}\n\n"
        f"Suggested web query if the manual is silent: {web_query or '(none)'}"
        + (f"\n\nCORRECTIONS FROM THE VERIFIER (fix all of these):\n{correction}" if correction else "")
    )
    # The basic web search variant returns citations with the quoted passage text, which the
    # verifier needs. The dynamic-filtering variant (web_search_20260209) strips citations.
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    kwargs: dict[str, Any] = dict(
        model=settings.chat_model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        tools=tools,
        output_config={"effort": "medium"},
    )
    try:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    except anthropic.BadRequestError as exc:
        log.warning("draft: fallbacks/web search rejected (%s); retrying plain", exc.message)
        kwargs.pop("output_config", None)
        try:
            response = client.messages.create(**kwargs)
        except anthropic.BadRequestError:
            kwargs.pop("tools", None)
            response = client.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        return "", [], "refusal"
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
    return text.strip(), _web_passages(response), str(getattr(response, "model", settings.chat_model))


def _refuse(retrieved: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "answer": (
            "I don't have a grounded answer for that in the ingested manual or a source I could verify. "
            f"{reason} I will not invent a number."
        ),
        "refused": True,
        "retrieved": retrieved,
        "web_sources": [],
        "verdict": None,
        "model": None,
    }


def generate_answer(
    question: str,
    retrieved: dict[str, Any],
    vehicle: dict[str, Any] | None,
    context: str = "",
    restated: str = "",
    web_query: str = "",
) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt_context = _format_context(retrieved, vehicle, context, restated)
    correction = ""
    verdict = None
    text, web, model_used = "", [], ""
    attempts: list[dict[str, Any]] = []
    blocking: list[str] = []
    web_notes: list[str] = []
    for attempt in range(2):
        text, web, model_used = _draft(client, question, prompt_context, web_query, correction)
        if model_used == "refusal" or not text:
            return _refuse(retrieved, "The model declined to answer.")
        ok, missing = numbers_are_grounded(text, retrieved, vehicle, question, web)
        verdict = verify(text, vehicle, retrieved.get("chunks") or [], web, specs=retrieved.get("specs") or [], manual=retrieved.get("manual"))
        blocking = list(verdict.manual_problems) if verdict else []
        web_notes = list(verdict.web_problems) if verdict else []
        if missing:
            blocking.append(f"These figures appear in no source at all: {', '.join(missing)}.")
        attempts.append({"draft": text, "manual_problems": blocking, "web_problems": web_notes, "web": len(web)})
        if not blocking and not web_notes:
            break
        correction = "\n".join(f"- {p}" for p in blocking + web_notes) + (
            "\nRemove every claim you cannot back with a manual page or a cited web passage that "
            "literally contains it. A shorter, fully supported answer beats a fuller one."
        )
        log.info("answer attempt %d: %d manual problems, %d web problems", attempt + 1, len(blocking), len(web_notes))
        if not blocking:
            break  # only web excerpts are short of the claim: deliver, and say so below
    if blocking:
        refusal = _refuse(retrieved, "A verifier found manual figures the pages do not support: " + " ".join(blocking)[:300])
        refusal["attempts"] = attempts
        return refusal
    if web_notes:
        text += "\n\nVerifier note. These web statements could not be confirmed from the cited excerpts; treat them as unverified: " + " | ".join(n[:200] for n in web_notes)
    return {
        "attempts": attempts,
        "answer": text,
        "refused": False,
        "retrieved": retrieved,
        "web_sources": web,
        "verdict": verdict.model_dump() if verdict else None,
        "model": model_used,
    }
