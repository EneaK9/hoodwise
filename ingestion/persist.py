"""Write extraction / chunk / spec / embedding results into Postgres."""

from __future__ import annotations

from pathlib import Path

from psycopg.types.json import Json

from app.db import get_conn
from ingestion.chunk import TextChunk
from ingestion.extract import ExtractedDocument
from ingestion.models import ExtractedSpec


def upsert_document(doc: ExtractedDocument, vehicle_id: str | None = None) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE documents
                   SET page_count = %s,
                       file_hash = %s,
                       status = 'extracted',
                       section_name = %s,
                       filename = %s,
                       vehicle_id = COALESCE(%s, vehicle_id)
                 WHERE doc_id = %s
             RETURNING id
                """,
                (len(doc.pages), doc.file_hash, doc.section_name, doc.filename, vehicle_id, doc.doc_id),
            )
            row = cur.fetchone()
            if row:
                document_id = str(row["id"])
            else:
                cur.execute(
                    """
                    INSERT INTO documents (vehicle_id, doc_id, filename, section_name, page_count, file_hash, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'extracted')
                    RETURNING id
                    """,
                    (vehicle_id, doc.doc_id, doc.filename, doc.section_name, len(doc.pages), doc.file_hash),
                )
                document_id = str(cur.fetchone()["id"])

            cur.execute("DELETE FROM pages WHERE document_id = %s", (document_id,))
            cur.execute("DELETE FROM diagrams WHERE document_id = %s", (document_id,))
            for page in doc.pages:
                cur.execute(
                    """
                    INSERT INTO pages (document_id, page_number, text_content, char_count, has_text_layer, render_path)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        page.page_number,
                        page.text,
                        page.char_count,
                        page.has_text_layer,
                        page.render_path,
                    ),
                )
                for im in page.images:
                    if im.discarded or not im.image_path:
                        continue
                    cur.execute(
                        """
                        INSERT INTO diagrams (document_id, page_number, image_path, bbox, phash, classification)
                        VALUES (%s, %s, %s, %s, %s, 'unknown')
                        """,
                        (
                            document_id,
                            page.page_number,
                            im.image_path,
                            Json(im.bbox) if im.bbox else None,
                            im.phash,
                        ),
                    )
        conn.commit()
    return document_id


def persist_chunks(document_id: str, chunks: list[TextChunk]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, page_number, section_path, chunk_type, content)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        chunk.page_number,
                        chunk.section_path,
                        chunk.chunk_type,
                        chunk.content,
                    ),
                )
        conn.commit()
    return len(chunks)


def persist_specs(document_id: str, rows: list[tuple[ExtractedSpec, str]]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM specs WHERE document_id = %s", (document_id,))
            for spec, status in rows:
                cur.execute(
                    """
                    INSERT INTO specs (
                      document_id, page_number, part_name, spec_type, value_nm, value_raw, unit,
                      torque_sequence, replace_required, condition_note, raw_context, source,
                      confidence, verification_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        spec.page_number,
                        spec.part_name,
                        spec.spec_type,
                        spec.value_nm,
                        spec.value_raw,
                        spec.unit,
                        spec.torque_sequence,
                        spec.replace_required,
                        spec.condition_note,
                        spec.raw_context,
                        spec.source,
                        spec.confidence,
                        status,
                    ),
                )
        conn.commit()
    return len(rows)


def start_run(document_id: str, stage: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_runs (document_id, stage, status)
                VALUES (%s, %s, 'running')
                RETURNING id
                """,
                (document_id, stage),
            )
            run_id = str(cur.fetchone()["id"])
        conn.commit()
    return run_id


def finish_run(run_id: str, ok: bool, error: str | None = None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_runs
                   SET status = %s, finished_at = now(), error = %s
                 WHERE id = %s
                """,
                ("ok" if ok else "failed", error, run_id),
            )
        conn.commit()


def update_diagram_caption(image_path: str, classification: str, caption: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE diagrams
                   SET classification = %s, caption = %s
                 WHERE image_path = %s
                """,
                (classification, caption, image_path),
            )
        conn.commit()


def persist_caption_chunk(document_id: str, page_number: int, caption: str) -> None:
    if not caption.strip():
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chunks (document_id, page_number, section_path, chunk_type, content)
                VALUES (%s, %s, %s, 'diagram_caption', %s)
                """,
                (document_id, page_number, "diagram", caption),
            )
        conn.commit()


def write_local_manifest(out_dir: Path, document_id: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "document_id.txt").write_text(document_id, encoding="utf-8")
