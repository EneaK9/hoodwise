"""Download official Hyundai UK owner's manuals listed on hyundai.com.

These are owner handbooks, not workshop/service manuals.
Source: https://www.hyundai.com/uk/en/owners/owning-a-hyundai/owners-manuals.html
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ingestion" / "catalogs" / "hyundai_uk_owners.json"


def _slug(item: dict) -> str:
    stem = unquote(item["url"].rsplit("/", 1)[-1])
    stem = re.sub(r"pdf$", "", stem, flags=re.I)
    text = f"{item['model']} {item['label']} {stem}"
    text = re.sub(r"[^\w]+", "_", text, flags=re.ASCII).strip("_")
    return text[:160] or "manual"


def download_one(item: dict, dest_dir: Path) -> Path:
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{_slug(item)}.pdf"
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    req = Request(item["url"], headers={"User-Agent": "Mozilla/5.0 HoodwiseManualFetch/1.0"})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" not in ctype and not data.startswith(b"%PDF"):
        raise RuntimeError(f"Not a PDF: {item['label']}")
    dest.write_bytes(data)
    return dest


def main() -> int:
    only = [a.lower() for a in sys.argv[1:]]
    catalog = json.loads(CATALOG.read_text())
    dest_dir = ROOT / catalog.get("folder", "manuals")
    source = catalog.get("source") or ""
    rows = catalog["manuals"]
    if only:
        rows = [
            r
            for r in rows
            if any(term in f"{r['model']} {r['label']}".lower() for term in only)
        ]
    print(f"source {source}", flush=True)
    print(f"downloading {len(rows)} manuals -> {dest_dir}", flush=True)
    failed = 0
    for row in rows:
        label = f"{row['model']} {row['label']}"
        try:
            path = download_one(row, dest_dir)
            print(f"ok {path.name} ({path.stat().st_size} bytes)", flush=True)
        except Exception as exc:
            failed += 1
            print(f"fail {label}: {exc}", flush=True)
    if failed:
        print(f"done with {failed} failures", flush=True)
        return 1
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
