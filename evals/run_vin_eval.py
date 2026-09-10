"""Per-field precision / recall for VIN identification against known ground truth.

    python -m evals.run_vin_eval

A field counts as a true positive when the decoder asserts the truth value, a false
positive when it asserts a different value or asserts something that must stay unknown,
and a false negative when it leaves a truth field empty that is not in unknown_ok.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from app.vindecode import identify

CASES = Path(__file__).parent / "vin_cases.yaml"
REPORT = Path(__file__).parent / "vin_eval_report.json"


def _norm(v) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", str(v).lower())  # "SANTAFE" and "Santa Fe" are the same fact


def main() -> int:
    cases = yaml.safe_load(CASES.read_text())["cases"]
    tp = fp = fn = 0
    rows = []
    for c in cases:
        ident = identify(c["vin"], None)
        notes = []
        if c.get("invalid"):
            ok = not ident["valid"]
            tp += int(ok); fp += int(not ok)
            print(f"{'OK ' if ok else 'BAD'} {c['id']:34} invalid VIN {'rejected' if ok else 'ACCEPTED'}")
            rows.append({"id": c["id"], "ok": ok})
            continue
        fields = ident["fields"]
        for field, want in (c.get("truth") or {}).items():
            got = fields.get(field, {}).get("value")
            if got is None:
                if field in (c.get("unknown_ok") or []):
                    continue
                fn += 1; notes.append(f"missing {field}")
            elif _norm(got) == _norm(want) or _norm(want) in _norm(got):
                tp += 1
            else:
                fp += 1; notes.append(f"{field}: got {got!r} want {want!r} ({fields[field]['source']})")
        for field in c.get("must_be_unknown") or []:
            if field in fields:
                fp += 1; notes.append(f"{field} asserted {fields[field]['value']!r} but must stay unknown")
        status = "OK " if not notes else "BAD"
        print(f"{status} {c['id']:34} {'; '.join(notes)}")
        rows.append({"id": c["id"], "fields": {k: v for k, v in fields.items()}, "notes": notes})
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    print(f"\nTP={tp} FP={fp} FN={fn}  precision={precision:.3f} recall={recall:.3f} F1={f1:.3f}")
    REPORT.write_text(json.dumps({"precision": precision, "recall": recall, "f1": f1, "rows": rows}, indent=1, ensure_ascii=False, default=str))
    return 0 if fp == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
