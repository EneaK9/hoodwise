"""Upsert golden YAML questions into eval_questions."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.db import get_conn

GOLDEN = Path(__file__).parent / "golden_questions.yaml"


def main() -> None:
    questions = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["questions"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for q in questions:
                cur.execute(
                    """
                    INSERT INTO eval_questions (question, category, expected_part, expected_value_raw, expected_substring, variant_hint)
                    SELECT %s, %s, %s, %s, %s, %s
                     WHERE NOT EXISTS (SELECT 1 FROM eval_questions WHERE question = %s)
                    """,
                    (
                        q["question"],
                        q.get("category") or "other",
                        q.get("expected_part"),
                        q.get("expected_value_raw"),
                        q.get("expected_substring"),
                        q.get("variant_hint"),
                        q["question"],
                    ),
                )
        conn.commit()
    print(f"eval questions loaded: {len(questions)}")


if __name__ == "__main__":
    main()
