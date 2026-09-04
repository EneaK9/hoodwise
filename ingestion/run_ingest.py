"""CLI: python -m ingestion.run_ingest --pdf SM_27.pdf [--vision] [--embed]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import settings
from app.db import fetch_one
from ingestion.chunk import chunk_document
from ingestion.embed import embed_document
from ingestion.extract import _file_sha256, extract_pdf, print_report
from ingestion.persist import (
    finish_run,
    persist_caption_chunk,
    persist_chunks,
    persist_specs,
    start_run,
    update_diagram_caption,
    upsert_document,
)
from ingestion.catalog import catalog_targets, ensure_catalog_document
from ingestion.section_map import DAMAGED_DOC_IDS, MVP_DOC_IDS
from ingestion.specs import dump_specs, extract_document_specs
from ingestion.vision import classify_image


def repair_pdfs(manual_dir: Path) -> None:
    import shutil
    import subprocess

    qpdf = shutil.which("qpdf")
    if not qpdf:
        print("qpdf not found — skip repair. brew install qpdf")
        return
    repaired_dir = Path(settings.data_dir) / "repaired"
    repaired_dir.mkdir(parents=True, exist_ok=True)
    for doc_id in DAMAGED_DOC_IDS:
        src = manual_dir / f"{doc_id}.pdf"
        dest = repaired_dir / f"{doc_id}.pdf"
        if not src.exists():
            print(f"missing {src}")
            continue
        result = subprocess.run(
            [qpdf, str(src), str(dest)],
            check=False,
            capture_output=True,
            text=True,
        )
        # qpdf uses exit 3 for warnings-but-wrote-output
        if result.returncode in (0, 3) and dest.exists():
            print(f"repaired {doc_id} -> {dest} (exit {result.returncode})")
        else:
            print(f"qpdf {doc_id} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")


def _already_embedded(doc_id: str, pdf_path: Path) -> bool:
    row = fetch_one(
        """
        SELECT d.file_hash,
               (SELECT count(*) FROM chunks c
                 WHERE c.document_id = d.id AND c.embedding IS NOT NULL) AS n
          FROM documents d
         WHERE d.doc_id = %s
        """,
        (doc_id,),
    )
    if not row or not row["n"]:
        return False
    return row["file_hash"] == _file_sha256(pdf_path) and int(row["n"]) > 50


def ingest_one(
    pdf_path: Path,
    use_vision: bool,
    do_embed: bool,
    *,
    skip_images: bool = False,
    vehicle_id: str | None = None,
    doc_id: str | None = None,
    section_name: str | None = None,
) -> None:
    print(f"\nExtracting {pdf_path} ...")
    extracted = extract_pdf(
        pdf_path,
        skip_images=skip_images,
        doc_id=doc_id,
        section_name=section_name,
    )
    print_report(extracted)
    document_id = upsert_document(extracted, vehicle_id=vehicle_id)
    run_id = start_run(document_id, "extract")
    try:
        chunks = chunk_document(extracted)
        persist_chunks(document_id, chunks)
        print(f"chunks written: {len(chunks)}")

        if use_vision:
            for page in extracted.pages:
                for im in page.images:
                    if im.discarded or not im.image_path:
                        continue
                    classified = classify_image(Path(im.image_path))
                    if classified is None:
                        continue
                    update_diagram_caption(
                        im.image_path, classified.classification, classified.caption
                    )
                    if classified.classification != "decorative" and classified.caption:
                        persist_caption_chunk(document_id, page.page_number, classified.caption)

        spec_rows = extract_document_specs(extracted, use_vision=use_vision)
        persist_specs(document_id, spec_rows)
        out = Path(settings.data_dir) / "extract" / extracted.doc_id / "specs.json"
        dump_specs(out, spec_rows)
        print(f"specs written: {len(spec_rows)}  ({out})")

        if do_embed:
            n = embed_document(document_id)
            print(f"embedded chunks: {n}")
        finish_run(run_id, True)
    except Exception as exc:
        finish_run(run_id, False, str(exc))
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hoodwise manual ingestion")
    parser.add_argument("--pdf", help="PDF filename or path (e.g. SM_27.pdf)")
    parser.add_argument("--mvp", action="store_true", help="Ingest SM_2, SM_22, SM_27")
    parser.add_argument("--repair", action="store_true", help="qpdf-repair damaged PDFs")
    parser.add_argument("--vision", action="store_true", help="Run Claude vision classify + spec extract")
    parser.add_argument("--embed", action="store_true", help="Embed chunks after extract")
    parser.add_argument(
        "--catalog",
        nargs="?",
        const="*",
        metavar="FILE",
        help="Ingest manuals from ingestion/catalogs/*.json (or one catalog file)",
    )
    args = parser.parse_args(argv)

    manual_dir = Path(settings.manual_dir)
    if args.repair:
        repair_pdfs(manual_dir)
        if not args.pdf and not args.mvp:
            return 0

    targets: list[Path] = []
    if args.mvp:
        targets = [manual_dir / f"{doc_id}.pdf" for doc_id in MVP_DOC_IDS]
    elif args.catalog:
        catalog_path = None if args.catalog == "*" else Path(args.catalog)
        targets_meta = catalog_targets(catalog_path)
        if not targets_meta:
            print("no catalog manuals found", file=sys.stderr)
            return 1
        failed = 0
        for path, meta in targets_meta:
            vehicle_id = ensure_catalog_document(meta)
            if _already_embedded(meta["doc_id"], path):
                print(f"skip {meta['doc_id']} (already embedded)")
                continue
            try:
                ingest_one(
                    path,
                    use_vision=False,
                    do_embed=args.embed,
                    skip_images=True,
                    vehicle_id=vehicle_id,
                    doc_id=meta["doc_id"],
                    section_name=meta["section_name"],
                )
            except Exception as exc:
                failed += 1
                print(f"fail {meta['doc_id']}: {exc}", file=sys.stderr)
        return 1 if failed else 0
    elif args.pdf:
        p = Path(args.pdf)
        targets = [p if p.exists() else manual_dir / p.name]
    else:
        parser.error("pass --pdf, --mvp, --catalog, or --repair")

    for path in targets:
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        ingest_one(path, use_vision=args.vision, do_embed=args.embed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
