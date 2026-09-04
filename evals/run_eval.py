"""Run golden questions through retrieve+answer and persist eval_runs."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.answer import generate_answer
from app.db import get_conn

GOLDEN = Path(__file__).parent / "golden_questions.yaml"


def main() -> None:
    questions = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["questions"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO eval_runs (config) VALUES (%s) RETURNING id", (json.dumps({"n": len(questions)}),))
            run_id = cur.fetchone()["id"]
            passed = 0
            for q in questions:
                result = generate_answer(q["question"], None)
                blob = result["answer"] + " " + " ".join(
                    s.get("value_raw") or "" for s in result["retrieved"]["specs"]
                )
                if q.get("expect_refuse"):
                    ok = result["refused"] or "will not" in result["answer"].lower() or "don't have" in result["answer"].lower()
                else:
                    needle = q.get("expected_substring")
                    ok = (needle in blob) if needle else bool(result["retrieved"]["specs"] or result["retrieved"]["chunks"])
                passed += int(ok)
                cur.execute(
                    """
                    INSERT INTO eval_results (run_id, question_id, passed, retrieved_ids, answer_text, notes)
                    SELECT %s, id, %s, %s::jsonb, %s, %s
                      FROM eval_questions
                     WHERE question = %s
                     LIMIT 1
                    """,
                    (
                        run_id,
                        ok,
                        json.dumps({"specs": [str(s["id"]) for s in result["retrieved"]["specs"]]}),
                        result["answer"],
                        q["id"],
                        q["question"],
                    ),
                )
            cur.execute("UPDATE eval_runs SET finished_at = now() WHERE id = %s", (run_id,))
        conn.commit()
    print(f"eval run {run_id}: {passed}/{len(questions)} passed")


if __name__ == "__main__":
    main()
