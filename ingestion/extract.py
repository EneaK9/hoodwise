"""PyMuPDF extraction: text, images, bboxes, page renders, perceptual-hash dedupe."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

from app.config import settings
from ingestion.hyundai import describe_pdf
from ingestion.section_map import SECTIONS, doc_id_from_filename

try:
    import imagehash
except ImportError:  # pragma: no cover
    imagehash = None


DECORATIVE_MIN_PX = 32
WATERMARK_PHASHES = set()


@dataclass
class ExtractedImage:
    page_number: int
    image_path: str
    bbox: list[float] | None
    phash: str | None
    width: int
    height: int
    xref: int
    discarded: bool = False
    discard_reason: str | None = None


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    char_count: int
    has_text_layer: bool
    render_path: str | None
    images: list[ExtractedImage] = field(default_factory=list)


@dataclass
class ExtractedDocument:
    doc_id: str
    filename: str
    section_name: str
    file_hash: str
    pages: list[ExtractedPage]
    unique_images: int
    discarded_images: int


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_phash(image: Image.Image) -> str | None:
    if imagehash is None:
        return None
    try:
        return str(imagehash.phash(image))
    except Exception:
        return None


def _is_decorative(width: int, height: int, phash: str | None) -> tuple[bool, str | None]:
    if width < DECORATIVE_MIN_PX or height < DECORATIVE_MIN_PX:
        return True, "tiny"
    if phash and phash in WATERMARK_PHASHES:
        return True, "known_watermark"
    return False, None


def extract_pdf(
    pdf_path: Path,
    out_dir: Path | None = None,
    *,
    skip_images: bool = False,
) -> ExtractedDocument:
    pdf_path = Path(pdf_path)
    hyundai = describe_pdf(pdf_path)
    if hyundai:
        doc_id = hyundai["doc_id"]
        section = hyundai["section_name"]
    else:
        doc_id = doc_id_from_filename(pdf_path.name)
        section = SECTIONS.get(doc_id, "Unknown")
    out_dir = out_dir or Path(settings.data_dir) / "extract" / doc_id
    img_dir = out_dir / "images"
    render_dir = out_dir / "renders"
    img_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)

    seen_phash: dict[str, str] = {}
    unique = 0
    discarded = 0
    pages: list[ExtractedPage] = []

    document = pymupdf.open(pdf_path)
    try:
        for i, page in enumerate(document):
            page_number = i + 1
            text = page.get_text() or ""
            char_count = len(text.strip())
            has_text = char_count >= 20
            render_path = None
            if not has_text:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                render_path = str(render_dir / f"page_{page_number:04d}.png")
                pix.save(render_path)

            extracted_images: list[ExtractedImage] = []
            if skip_images:
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        text=text,
                        char_count=char_count,
                        has_text_layer=has_text,
                        render_path=render_path,
                        images=[],
                    )
                )
                continue
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                try:
                    info = document.extract_image(xref)
                except Exception:
                    continue
                ext = info.get("ext", "png")
                width = int(info.get("width") or 0)
                height = int(info.get("height") or 0)
                image_bytes = info["image"]

                bbox = None
                for rect in page.get_image_rects(xref):
                    bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
                    break

                tmp = img_dir / f"p{page_number:04d}_{img_index:02d}_{xref}.{ext}"
                tmp.write_bytes(image_bytes)

                phash = None
                try:
                    with Image.open(tmp) as im:
                        phash = _safe_phash(im.convert("RGB"))
                except Exception:
                    phash = None

                discard, reason = _is_decorative(width, height, phash)
                if not discard and phash and phash in seen_phash:
                    discard, reason = True, "duplicate_phash"

                if discard:
                    discarded += 1
                    tmp.unlink(missing_ok=True)
                    extracted_images.append(
                        ExtractedImage(
                            page_number=page_number,
                            image_path=seen_phash.get(phash or "", ""),
                            bbox=bbox,
                            phash=phash,
                            width=width,
                            height=height,
                            xref=xref,
                            discarded=True,
                            discard_reason=reason,
                        )
                    )
                    continue

                unique += 1
                if phash:
                    seen_phash[phash] = str(tmp)
                extracted_images.append(
                    ExtractedImage(
                        page_number=page_number,
                        image_path=str(tmp),
                        bbox=bbox,
                        phash=phash,
                        width=width,
                        height=height,
                        xref=xref,
                    )
                )

            pages.append(
                ExtractedPage(
                    page_number=page_number,
                    text=text,
                    char_count=char_count,
                    has_text_layer=has_text,
                    render_path=render_path,
                    images=extracted_images,
                )
            )
    finally:
        document.close()

    result = ExtractedDocument(
        doc_id=doc_id,
        filename=pdf_path.name,
        section_name=section,
        file_hash=_file_sha256(pdf_path),
        pages=pages,
        unique_images=unique,
        discarded_images=discarded,
    )
    (out_dir / "extract.json").write_text(
        json.dumps(_to_jsonable(result), indent=2),
        encoding="utf-8",
    )
    return result


def _to_jsonable(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    return obj


def print_report(doc: ExtractedDocument) -> None:
    ocr_pages = sum(1 for p in doc.pages if not p.has_text_layer)
    print(f"\n=== {doc.doc_id} — {doc.section_name} ===")
    print(f"pages={len(doc.pages)}  unique_images={doc.unique_images}  discarded={doc.discarded_images}  low-text={ocr_pages}")
    for page in doc.pages:
        kept = sum(1 for im in page.images if not im.discarded)
        print(
            f"  p{page.page_number:03d}  chars={page.char_count:5d}  "
            f"images={kept}/{len(page.images)}  text_layer={'yes' if page.has_text_layer else 'NO'}"
        )
