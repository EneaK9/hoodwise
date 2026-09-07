"""Precision / recall / F1 over the hard question set, end to end through the chat API.

Runs in-process against the FastAPI app (needs Postgres with ingested manuals and an
Anthropic key). Every question's `expect` substrings are positives to recover; every
`forbid` substring found, every wrong-turn (answered when it should have asked, asked
when it should have answered, refused when it should have answered) is a false positive.

    python -m evals.run_hard_eval            # all questions
    python -m evals.run_hard_eval sf-        # ids starting with "sf-"
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.config import settings
from app.main import app

HARD = Path(__file__).parent / "hard_questions.yaml"
REPORT = Path(__file__).parent / "hard_eval_report.json"


class ForbidJudgement(BaseModel):
    asserted_as_this_cars_figure: list[bool] = Field(
        description="One entry per forbidden value, in order: true if the answer presents that value as the figure for the user's car; false if it only mentions it to say it belongs to another engine, fuel, trim or row"
    )


def _forbidden_asserted(answer: str, forbidden: list[str]) -> list[bool]:
    """A forbidden figure that the answer explicitly attributes to another row is not an error.
    A model judges that distinction; substring matching cannot."""
    if not forbidden or not settings.anthropic_api_key:
        return [True] * len(forbidden)
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.parse(
            model=settings.understand_model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "ANSWER:\n" + answer + "\n\nFORBIDDEN VALUES (each appears somewhere in the answer): "
                    + json.dumps(forbidden)
                    + "\n\nFor each value, does the answer present it as the figure for the user's own car? "
                    "Answer false when the text says the value belongs to a different engine, fuel, trim or table row."
                ),
            }],
            output_format=ForbidJudgement,
            output_config={"effort": "low"},
        )
        out = response.parsed_output.asserted_as_this_cars_figure if response.parsed_output else []
    except Exception:
        out = []
    if len(out) != len(forbidden):
        return [True] * len(forbidden)
    return out


def _norm(text: str) -> str:
    text = (text or "").lower().replace(",", ".")
    for dash in ("‐", "‑", "‒", "–", "—", "−"):
        text = text.replace(dash, "-")
    return " ".join(text.replace("~", "-").split())


def _run_one(client: TestClient, q: dict) -> dict:
    session_id = None
    for prior in [p.strip() for p in (q.get("context") or "").split("|") if p.strip()]:
        r = client.post("/api/chat", json={"message": prior, "vin": q.get("vin"), "session_id": session_id})
        session_id = r.json()["session_id"]
    t = time.time()
    r = client.post("/api/chat", json={"message": q["question"], "vin": q.get("vin"), "session_id": session_id})
    j = r.json()
    return {
        "id": q["id"],
        "seconds": round(time.time() - t, 1),
        "answer": j.get("answer", ""),
        "clarify": bool(j.get("clarify")),
        "refused": bool(j.get("refused")),
        "vehicle": (j.get("vehicle") or {}).get("label"),
        "web_sources": len([c for c in j.get("citations") or [] if c.get("kind") == "web"]),
        "verdict": j.get("verdict"),
    }


def _score(q: dict, out: dict) -> dict:
    tp = fp = fn = 0
    notes: list[str] = []
    text = _norm(out["answer"])
    if q.get("expect_clarify"):
        if out["clarify"]:
            tp += 1
        else:
            fp += 1
            notes.append("answered instead of asking")
    elif q.get("expect_refuse"):
        if out["refused"]:
            tp += 1
        else:
            fp += 1
            notes.append("answered instead of refusing")
    elif q.get("expect_clarify_or_refuse"):
        if out["clarify"] or out["refused"]:
            tp += 1
        else:
            fp += 1
            notes.append("answered a car we do not have")
    else:
        if out["clarify"]:
            fn += len(q.get("expect", []))
            notes.append("asked instead of answering")
        elif out["refused"]:
            fn += len(q.get("expect", []))
            notes.append("refused")
        else:
            for needle in q.get("expect", []):
                if _norm(needle) in text:
                    tp += 1
                else:
                    fn += 1
                    notes.append(f"missing {needle!r}")
            # expect_any: one positive, satisfied by any of several printed forms (kgf·m vs N·m).
            for group in q.get("expect_any", []):
                if any(_norm(alt) in text for alt in group):
                    tp += 1
                else:
                    fn += 1
                    notes.append(f"missing any of {group!r}")
            present = [needle for needle in q.get("forbid", []) if _norm(needle) in text]
            if present:
                for needle, asserted in zip(present, _forbidden_asserted(out["answer"], present)):
                    if asserted:
                        fp += 1
                        notes.append(f"asserts forbidden {needle!r} as this car's figure")
                    else:
                        notes.append(f"mentions {needle!r} only to exclude it")
    return {"tp": tp, "fp": fp, "fn": fn, "notes": notes}


def main(prefix: str = "") -> int:
    questions = [q for q in yaml.safe_load(HARD.read_text())["questions"] if q["id"].startswith(prefix)]
    client = TestClient(app)
    rows = []
    tp = fp = fn = 0
    for q in questions:
        out = _run_one(client, q)
        score = _score(q, out)
        tp += score["tp"]; fp += score["fp"]; fn += score["fn"]
        status = "OK " if not score["fp"] and not score["fn"] else "BAD"
        print(f"{status} {q['id']:32} tp={score['tp']} fp={score['fp']} fn={score['fn']} {out['seconds']:>5}s  {'; '.join(score['notes'])}")
        rows.append({**out, **score, "question": q["question"]})
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    perfect = sum(1 for r in rows if not r["fp"] and not r["fn"])
    print("\n" + "=" * 72)
    print(f"questions={len(rows)}  fully correct={perfect}  TP={tp} FP={fp} FN={fn}")
    print(f"precision={precision:.3f}  recall={recall:.3f}  F1={f1:.3f}")
    REPORT.write_text(json.dumps({"precision": precision, "recall": recall, "f1": f1, "rows": rows}, indent=1, ensure_ascii=False))
    print(f"report: {REPORT}")
    return 0 if f1 >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
