from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.answer import generate_answer
from app.retrieval import retrieve
from app.shop import shop_links
from app.snippets import attach_snippets, render_snippet, snippet_needles
from app.resolve import public_vehicle, resolve_vehicle, vehicle_catalog_with_years
from app.understand import mention_dict, understand
from app.auth import (
    COOKIE_NAME,
    create_user,
    current_user,
    issue_session,
    logout,
    require_user,
    verify_login,
)
from app.config import settings
from app.db import fetch_all, fetch_one, get_conn
from app.ratelimit import limit_chat, limit_vin
from app.schemas import ChatIn, LoginIn, SessionClaimIn, SignupIn, VehiclePickIn, VinFuelIn, VinIn
from app.vin import decode_vin, find_vins, set_vin_fuel

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("hoodwise")

app = FastAPI(title="Hoodwise", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

image_root = Path(settings.image_dir)
image_root.mkdir(parents=True, exist_ok=True)
data_root = Path(settings.data_dir)
data_root.mkdir(parents=True, exist_ok=True)
if data_root.exists():
    app.mount("/media", StaticFiles(directory=str(data_root)), name="media")


def _public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    return {"id": str(user["id"]), "email": user["email"]}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/me")
def me(user: dict | None = Depends(current_user)) -> dict:
    return {"user": _public_user(user)}


@app.post("/api/auth/signup")
def signup(body: SignupIn, request: Request, response: Response) -> dict:
    user = create_user(body.email, body.password)
    issue_session(response, user["id"], request.headers.get("user-agent"))
    log.info("signup ok")
    return {"user": _public_user(user)}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    user = verify_login(body.email, body.password)
    issue_session(response, user["id"], request.headers.get("user-agent"))
    log.info("login ok")
    return {"user": _public_user(user)}


@app.post("/api/auth/logout")
def auth_logout(
    response: Response,
    hoodwise_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict:
    logout(response, hoodwise_session)
    return {"ok": True}


@app.post("/api/auth/claim")
def claim_session(body: SessionClaimIn, user: dict | None = Depends(current_user)) -> dict:
    require_user(user)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat_sessions
                   SET user_id = %s
                 WHERE id = %s AND user_id IS NULL
                """,
                (user["id"], body.session_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Session not found or already claimed")
        conn.commit()
    return {"ok": True}


@app.get("/api/vehicles")
def list_vehicles() -> dict:
    rows = fetch_all(
        """
        SELECT v.id AS vehicle_id, v.make, v.model, v.generation, v.chassis, v.year_from, v.year_to,
               vv.id AS variant_id, vv.engine_code, vv.engine_label, vv.transmission, vv.trim, vv.body, vv.market
          FROM vehicles v
          JOIN vehicle_variants vv ON vv.vehicle_id = v.id
         ORDER BY vv.engine_label, vv.body, vv.transmission
        """
    )
    return {"variants": [{**r, "vehicle_id": str(r["vehicle_id"]), "variant_id": str(r["variant_id"])} for r in rows]}


@app.get("/api/garage")
def garage(user: dict | None = Depends(current_user)) -> dict:
    require_user(user)
    rows = fetch_all(
        """
        SELECT uv.id, uv.nickname, uv.vin IS NOT NULL AS has_vin,
               vv.id AS variant_id, vv.engine_label, vv.transmission, vv.trim, vv.body
          FROM user_vehicles uv
          JOIN vehicle_variants vv ON vv.id = uv.variant_id
         WHERE uv.user_id = %s
         ORDER BY uv.created_at DESC
        """,
        (user["id"],),
    )
    return {"vehicles": [{**r, "id": str(r["id"]), "variant_id": str(r["variant_id"])} for r in rows]}


@app.post("/api/garage")
def add_garage(body: VehiclePickIn, user: dict | None = Depends(current_user)) -> dict:
    require_user(user)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_vehicles (user_id, variant_id, vin, nickname)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user["id"], body.variant_id, body.vin, body.nickname),
            )
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"])}


@app.post("/api/vin/decode")
def vin_decode(body: VinIn, request: Request) -> dict:
    limit_vin(request)
    try:
        decoded = decode_vin(body.vin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid VIN") from exc
    log.info("vin decoded source=%s confidence=%s", decoded.get("source"), decoded.get("confidence"))
    pub = public_vehicle(decoded) or {}
    return {"decode": {**decoded, **pub}}


@app.post("/api/vin/fuel")
def vin_fuel(body: VinFuelIn, request: Request) -> dict:
    """The owner confirms petrol or diesel when no decoder could. Stored on the VIN."""
    limit_vin(request)
    try:
        decoded = set_vin_fuel(body.vin, body.fuel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid VIN") from exc
    if not decoded:
        raise HTTPException(status_code=404, detail="VIN not decoded")
    pub = public_vehicle(decoded) or {}
    return {"decode": {**decoded, **pub}}


@app.get("/api/sessions")
def list_sessions(user: dict | None = Depends(current_user)) -> dict:
    if not user:
        return {"sessions": []}
    rows = fetch_all(
        """
        SELECT id, title, variant_id, created_at
          FROM chat_sessions
         WHERE user_id = %s
         ORDER BY created_at DESC
         LIMIT 50
        """,
        (user["id"],),
    )
    return {
        "sessions": [
            {**r, "id": str(r["id"]), "variant_id": str(r["variant_id"]) if r["variant_id"] else None}
            for r in rows
        ]
    }


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: UUID, user: dict | None = Depends(current_user)) -> dict:
    session = fetch_one("SELECT id, user_id FROM chat_sessions WHERE id = %s", (session_id,))
    if not session:
        raise HTTPException(status_code=404, detail="Not found")
    if session["user_id"] and (not user or str(user["id"]) != str(session["user_id"])):
        raise HTTPException(status_code=403, detail="Forbidden")
    messages = fetch_all(
        """
        SELECT m.id, m.role, m.content, m.created_at
          FROM messages m
         WHERE m.session_id = %s
         ORDER BY m.created_at
        """,
        (session_id,),
    )
    citations = fetch_all(
        """
        SELECT c.message_id, c.spec_id, c.chunk_id, c.diagram_id, c.rank,
               s.part_name, s.value_raw, s.page_number, s.crop_path, s.condition_note,
               d.doc_id, d.section_name
          FROM message_citations c
          JOIN messages m ON m.id = c.message_id
          LEFT JOIN specs s ON s.id = c.spec_id
          LEFT JOIN documents d ON d.id = s.document_id
         WHERE m.session_id = %s
        """,
        (session_id,),
    )
    by_msg: dict[str, list] = {}
    for cit in citations:
        by_msg.setdefault(str(cit["message_id"]), []).append(
            {
                "spec_id": str(cit["spec_id"]) if cit["spec_id"] else None,
                "part_name": cit.get("part_name"),
                "value_raw": cit.get("value_raw"),
                "page_number": cit.get("page_number"),
                "crop_path": cit.get("crop_path"),
                "condition_note": cit.get("condition_note"),
                "doc_id": cit.get("doc_id"),
                "section_name": cit.get("section_name"),
            }
        )
    return {
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat(),
                "citations": by_msg.get(str(m["id"]), []),
            }
            for m in messages
        ]
    }


def _session_vin(session_id: str | None) -> str | None:
    if not session_id:
        return None
    row = fetch_one("SELECT vin FROM chat_sessions WHERE id = %s", (session_id,))
    return row["vin"] if row else None


def _ensure_session(body: ChatIn, user: dict | None, variant_id: str | None, vin: str | None) -> str:
    if body.session_id:
        row = fetch_one("SELECT id, user_id FROM chat_sessions WHERE id = %s", (body.session_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        if row["user_id"] and (not user or str(user["id"]) != str(row["user_id"])):
            raise HTTPException(status_code=403, detail="Forbidden")
        if variant_id or vin:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE chat_sessions SET variant_id = COALESCE(%s, variant_id), vin = COALESCE(%s, vin) WHERE id = %s",
                        (variant_id, vin, body.session_id),
                    )
                conn.commit()
        return body.session_id
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (user_id, variant_id, vin, title)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user["id"] if user else None,
                    variant_id,
                    vin,
                    body.message[:80],
                ),
            )
            session_id = str(cur.fetchone()["id"])
        conn.commit()
    return session_id


def _store_turn(session_id: str, question: str, result: dict) -> str:
    retrieved = result["retrieved"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s) RETURNING id",
                (session_id, question),
            )
            cur.execute(
                """
                INSERT INTO messages (session_id, role, content, model)
                VALUES (%s, 'assistant', %s, %s)
                RETURNING id
                """,
                (session_id, result["answer"], result.get("model")),
            )
            message_id = str(cur.fetchone()["id"])
            for rank, spec in enumerate(retrieved["specs"]):
                cur.execute(
                    """
                    INSERT INTO message_citations (message_id, spec_id, rank)
                    VALUES (%s, %s, %s)
                    """,
                    (message_id, spec["id"], rank),
                )
            for rank, chunk in enumerate(retrieved["chunks"]):
                cur.execute(
                    """
                    INSERT INTO message_citations (message_id, chunk_id, rank)
                    VALUES (%s, %s, %s)
                    """,
                    (message_id, chunk["id"], rank),
                )
            cur.execute(
                """
                INSERT INTO retrieval_logs (message_id, query, retrieval_mode, candidate_ids)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (
                    message_id,
                    question,
                    retrieved["mode"],
                    json.dumps(
                        {
                            "specs": [str(s["id"]) for s in retrieved["specs"]],
                            "chunks": [str(c["id"]) for c in retrieved["chunks"]],
                        }
                    ),
                ),
            )
        conn.commit()
    return message_id


def _citations(result: dict) -> list[dict]:
    from app.resolve import clean_part_name

    answer = result.get("answer") or ""
    out = []
    for spec in result["retrieved"]["specs"]:
        raw = spec.get("value_raw") or ""
        nm = spec.get("value_nm")
        if answer and raw.split()[0] not in answer and (nm is None or str(nm) not in answer):
            continue
        out.append(
            {
                "kind": "spec",
                "spec_id": str(spec["id"]),
                "part_name": clean_part_name(spec.get("part_name") or "", spec.get("raw_context") or ""),
                "value_raw": spec["value_raw"],
                "page_number": spec["page_number"],
                "doc_id": spec.get("doc_id"),
                "section_name": spec.get("section_name"),
                "condition_note": spec.get("condition_note"),
                "replace_required": spec.get("replace_required"),
                "crop_path": spec.get("crop_path"),
            }
        )
    return out


def _cited(result: dict, question: str) -> list[dict]:
    cited = attach_snippets(_citations(result), question, result.get("answer") or "", result.get("retrieved") or {})
    for w in result.get("web_sources") or []:
        cited.append({
            "kind": "web",
            "url": w.get("url"),
            "title": w.get("title") or w.get("url"),
            "cited_text": (w.get("cited_text") or "")[:400],
        })
    return cited


def _previous_user_message(session_id: str | None, turns: int = 3) -> str:
    """Recent user turns, oldest first. Clarification replies ("2016", "diesel") only make
    sense together with the question that came before them."""
    if not session_id:
        return ""
    rows = fetch_all(
        """
        SELECT content FROM messages
         WHERE session_id = %s AND role = 'user'
         ORDER BY created_at DESC LIMIT %s
        """,
        (session_id, turns),
    )
    return " | ".join((r.get("content") or "").strip() for r in reversed(rows) if r.get("content"))


def _clarify_result(ask: dict, kind: str) -> dict:
    return {
        "answer": ask["ask"],
        "refused": False,
        "clarify": {"missing": ask["missing"], "options": ask["options"]},
        "retrieved": {"mode": "clarify", "specs": [], "chunks": [], "kind": kind},
        "web_sources": [],
        "model": None,
        "shop": None,
    }


def _run_chat(body: ChatIn, request: Request, user: dict | None) -> tuple[str, dict, dict | None, list[str], str]:
    limit_chat(request)
    context = _previous_user_message(body.session_id)
    session_vin = _session_vin(body.session_id)

    # 1. The VIN, if any, pins the car before anything is interpreted.
    pinned = resolve_vehicle(body.message, body.vin, session_vin)
    pinned_vehicle = public_vehicle(pinned.get("decoded")) if pinned.get("vin") else None

    # 2. The model reads the question: part, car and engine, what is missing, what to ask.
    understanding = understand(body.message, context, pinned_vehicle, vehicle_catalog_with_years())
    if understanding is None:
        session_id = _ensure_session(body, user, pinned.get("variant_id") or body.variant_id, pinned.get("vin"))
        result = _clarify_result(
            {"missing": "other", "ask": "I could not read that. Which car is it, and what do you want to know?", "options": []},
            "",
        )
        message_id = _store_turn(session_id, body.message, result)
        return session_id, result, pinned_vehicle, find_vins(body.message), message_id

    mention = mention_dict(understanding) or {
        "engine": understanding.vehicle.engine,
        "fuel": understanding.vehicle.fuel,
        "fuel_basis": understanding.vehicle.fuel_basis,
        "displacement_l": understanding.vehicle.displacement_l,
        "manual_id": understanding.manual_id,
    }
    resolved = resolve_vehicle(body.message, body.vin, session_vin, mention=mention)
    vehicle = public_vehicle(resolved.get("decoded"))
    kind = understanding.part
    session_id = _ensure_session(body, user, resolved.get("variant_id") or body.variant_id, resolved.get("vin"))

    # 3. Ask before guessing.
    if understanding.missing and understanding.ask:
        ask = {"missing": understanding.missing[0], "ask": understanding.ask, "options": understanding.options}
        result = _clarify_result(ask, kind)
        message_id = _store_turn(session_id, body.message, result)
        log.info("chat clarify missing=%s part=%s", ask["missing"], kind or "-")
        return session_id, result, vehicle, find_vins(body.message), message_id

    # 4. Retrieve whole manual pages for the cleaned phrase, scoped to the pinned car.
    retrieved = retrieve(
        understanding.search_query or body.message,
        resolved.get("variant_id") or body.variant_id,
        vehicle_id=resolved.get("vehicle_id"),
    )
    retrieved["kind"] = kind

    # 5. Draft with manual + web search, verify, refuse if unsupported.
    result = generate_answer(
        body.message,
        retrieved,
        vehicle,
        context=context,
        restated=understanding.restated,
        web_query=understanding.web_query,
    )
    verdict = result.get("verdict") or {}
    result["shop"] = None if result.get("refused") else shop_links(verdict.get("shop_item"))
    message_id = _store_turn(session_id, body.message, result)
    log.info(
        "chat ok source=%s part=%s fuel=%s refused=%s",
        (resolved.get("decoded") or {}).get("source"), kind or "-",
        (vehicle or {}).get("fuel") or "-", result.get("refused"),
    )
    return session_id, result, vehicle, find_vins(body.message), message_id


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, user: dict | None = Depends(current_user)) -> dict:
    session_id, result, vehicle, detected, message_id = _run_chat(body, request, user)
    return {
        "session_id": session_id,
        "message_id": message_id,
        "answer": result["answer"],
        "refused": result["refused"],
        "mode": result["retrieved"]["mode"],
        "verdict": result.get("verdict"),
        "citations": _cited(result, body.message),
        "detected_vins": detected,
        "vehicle": vehicle,
        "model": result.get("model"),
        "shop": result.get("shop"),
        "clarify": result.get("clarify"),
        "needs_fuel": bool(result.get("needs_fuel")) or bool((vehicle or {}).get("needs_fuel_confirmation")),
    }


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, request: Request, user: dict | None = Depends(current_user)):
    session_id, result, vehicle, detected, message_id = _run_chat(body, request, user)

    def events():
        payload = {
            "session_id": session_id,
            "message_id": message_id,
            "answer": result["answer"],
            "refused": result["refused"],
            "citations": _cited(result, body.message),
            "detected_vins": detected,
            "vehicle": vehicle,
            "shop": result.get("shop"),
            "clarify": result.get("clarify"),
            "needs_fuel": bool(result.get("needs_fuel")) or bool((vehicle or {}).get("needs_fuel_confirmation")),
        }
        yield f"event: answer\ndata: {json.dumps(payload)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/manual/{doc_id}/page/{page_number}")
def manual_page(doc_id: str, page_number: int, q: str = "") -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,80}", doc_id):
        raise HTTPException(status_code=404, detail="Not found")
    if page_number < 1 or page_number > 2000:
        raise HTTPException(status_code=404, detail="Not found")
    needles = snippet_needles(q, "") if q else []
    path = render_snippet(doc_id, page_number, needles)
    if not path:
        raise HTTPException(status_code=404, detail="Not found")
    target = Path(path).resolve()
    root = Path(settings.data_dir).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


@app.get("/api/images/{path:path}")
def serve_image(path: str) -> FileResponse:
    root = Path(settings.data_dir).resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)
