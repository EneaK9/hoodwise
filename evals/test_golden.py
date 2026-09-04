"""Eval harness against the golden question set.

Retrieval tests need a live Postgres with ingested specs.
Policy tests (grounding / VIN / refuse) run without the database.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.answer import numbers_are_grounded
from app.vin import VIN_RE, find_vins

GOLDEN = Path(__file__).parent / "golden_questions.yaml"


def _questions() -> list[dict]:
    payload = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    return payload["questions"]


def test_golden_file_has_fifty() -> None:
    assert len(_questions()) >= 50


def test_vin_regex_detects_us_and_uk() -> None:
    text = "my vin is 19XFC1F79HE028859 and also SHHFK7H90JU210301 thanks"
    found = find_vins(text)
    assert "19XFC1F79HE028859" in found
    assert "SHHFK7H90JU210301" in found


def test_vin_rejects_i_o_q() -> None:
    assert VIN_RE.search("1HGBH41JXMN109186")  # X is valid
    assert not find_vins("THISISNOTAVINNUMBER")


def test_numbers_must_appear_in_citations() -> None:
    retrieved = {
        "specs": [{"value_raw": "35 N·m (3.6 kgf·m, 26 lbf·ft)", "part_name": "Brake hose", "condition_note": "", "raw_context": ""}],
        "chunks": [],
    }
    ok, missing = numbers_are_grounded("Tighten the brake hose to 35 N·m.", retrieved)
    assert ok
    assert missing == []
    ok, missing = numbers_are_grounded("Tighten to 99 N·m.", retrieved)
    assert not ok
    assert "99" in missing


def test_refuse_questions_are_marked() -> None:
    refuse = [q for q in _questions() if q.get("expect_refuse")]
    assert len(refuse) >= 2


@pytest.mark.integration
def test_retrieval_hits_golden_substrings() -> None:
    from app.db import fetch_one
    from app.retrieval import retrieve

    if fetch_one("SELECT 1 FROM specs LIMIT 1") is None:
        pytest.skip("no specs ingested")
    failures = []
    for q in _questions():
        if q.get("expect_refuse") or q["category"] == "procedure":
            continue
        result = retrieve(q["question"], None)
        blob = " ".join(
            [s.get("value_raw") or "" for s in result["specs"]]
            + [c.get("content") or "" for c in result["chunks"]]
        )
        needle = q.get("expected_substring")
        if needle and needle not in blob:
            failures.append(f"{q['id']}: missing {needle}")
    assert failures == [], "\n".join(failures)
