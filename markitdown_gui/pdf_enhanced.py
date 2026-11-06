"""PDF → Markdown with real table structure and extracted images (MarkItDown's PDF path drops both)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from markitdown_gui import ocr as ocr_engine
from markitdown_gui.assets import ImageSink

MIN_IMAGE_PT = 50          # skip icons/logos smaller than this (PDF points)
BACKGROUND_COVERAGE = 0.8  # image covering most of a text-heavy page is a background
BACKGROUND_MIN_CHARS = 200
IMAGE_DPI = 300            # base render DPI for figures (150 looked blurry once scaled up in the bundle PDF)
IMAGE_MAX_DPI = 600
IMAGE_TARGET_PX = 1600     # small figures get extra DPI so their long side reaches about this many pixels
IMAGE_MAX_PIXELS = 12_000_000
OCR_DPI = 300
SCANNED_PAGE_MAX_CHARS = 20  # fewer text-layer chars than this => treat page as scanned
MIN_TABLE_FILL = 0.6       # share of non-empty cells; below this a "table" is usually layout noise
CAPTION_RE = re.compile(r"^(ES\s+)?(Figure|Fig\.|Table|그림|표)\s*[\dA-Z]", re.IGNORECASE)


@dataclass
class PdfResult:
    markdown: str
    tables: int


@dataclass
class _Block:
    top: float
    bottom: float
    kind: str  # "table" | "image"
    content: str = ""
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    ocr: bool = False


def _cluster(values: list[float], tol: float = 3) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _clean_rows(rows: list[list[str | None]]) -> list[list[str]]:
    rows = [[re.sub(r"\s*\n\s*", " ", c or "").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    keep = [i for i in range(len(rows[0])) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    # Header/body grid offsets split one logical column into two complementary halves.
    col = 0
    while rows and col < len(rows[0]) - 1:
        if all(not (r[col] and r[col + 1]) for r in rows):
            rows = [r[:col] + [r[col] or r[col + 1]] + r[col + 2:] for r in rows]
        else:
            col += 1
    return rows


def _fill_ratio(rows: list[list[str]]) -> float:
    cells = [c for r in rows for c in r]
    return sum(1 for c in cells if c) / len(cells) if cells else 0.0


def _to_markdown_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [[c.replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _fill_rect_table(page):
    """Tables drawn with colored cell backgrounds instead of ruling lines."""
    cells = [
        r for r in page.rects
        if r.get("fill") and (r["x1"] - r["x0"]) > 40 and (r["bottom"] - r["top"]) > 20
    ]
    if len(cells) < 3:
        return None
    words = page.extract_words()
    if not words:
        return None
    top = min(r["top"] for r in cells)
    left = min(w["x0"] for w in words) - 1
    right = max(w["x1"] for w in words) + 1
    # Pull in an unfilled header row sitting directly above the first filled row.
    for _ in range(3):
        above = [w for w in words if top - 6 <= w["bottom"] <= top + 0.5 and w["top"] < top]
        if not above:
            break
        top = min(w["top"] for w in above) - 0.5
    bottom = max(r["bottom"] for r in cells) + 0.5
    settings = {
        "vertical_strategy": "explicit",
        "horizontal_strategy": "explicit",
        "explicit_vertical_lines": _cluster([left, right] + [r["x0"] for r in cells] + [r["x1"] for r in cells]),
        "explicit_horizontal_lines": _cluster([top, bottom] + [r["top"] for r in cells] + [r["bottom"] for r in cells]),
    }
    tables = page.find_tables(settings)
    return tables[0] if tables else None


def _page_tables(page) -> list[_Block]:
    candidates = []
    fill_table = _fill_rect_table(page)
    if fill_table is not None:
        candidates.append(fill_table)
    candidates += page.find_tables()

    blocks: list[_Block] = []
    for t in candidates:
        rows = _clean_rows(t.extract())
        if len(rows) < 2 or len(rows[0]) < 2 or _fill_ratio(rows) < MIN_TABLE_FILL:
            continue
        if any(_overlaps(t.bbox, b.bbox) for b in blocks):
            continue
        blocks.append(_Block(t.bbox[1], t.bbox[3], "table", _to_markdown_table(rows), t.bbox))
    return blocks


def _caption_near(lines: list[dict], top: float, bottom: float) -> str | None:
    best = None
    for ln in lines:
        text = ln["text"].strip()
        if not CAPTION_RE.match(text):
            continue
        dist = ln["top"] - bottom if ln["top"] >= bottom else top - ln["bottom"]
        if 0 <= dist <= 40 and (best is None or dist < best[0]):
            best = (dist, text)
    return best[1] if best else None


def _figure_dpi(w_pt: float, h_pt: float) -> int:
    long_in = max(w_pt, h_pt) / 72
    dpi = min(max(IMAGE_DPI, IMAGE_TARGET_PX / long_in), IMAGE_MAX_DPI)
    area_in = (w_pt / 72) * (h_pt / 72)
    dpi = min(dpi, (IMAGE_MAX_PIXELS / area_in) ** 0.5)
    return int(dpi)


def _page_images(page, page_no: int, sink: ImageSink, lines: list[dict]) -> list[_Block]:
    if not sink.enabled:
        return []
    page_area = float(page.width * page.height)
    blocks: list[_Block] = []
    for img in page.images:
        x0, top = max(img["x0"], 0), max(img["top"], 0)
        x1, bottom = min(img["x1"], page.width), min(img["bottom"], page.height)
        w, h = x1 - x0, bottom - top
        if w < MIN_IMAGE_PT or h < MIN_IMAGE_PT:
            continue
        if (w * h) / page_area > BACKGROUND_COVERAGE and len(page.chars) > BACKGROUND_MIN_CHARS:
            continue
        bbox = (x0, top, x1, bottom)
        if any(_overlaps(bbox, b.bbox) for b in blocks):
            continue
        alt = _caption_near(lines, top, bottom) or f"Image (page {page_no})"
        alt = alt.replace("[", "(").replace("]", ")")
        crop = page.crop(bbox)
        ocr_before = sink.ocr_count
        dpi = _figure_dpi(w, h)
        rendered = crop.to_image(resolution=dpi).original
        ocr_img = None
        if sink.ocr:
            ocr_img = rendered if dpi == OCR_DPI else crop.to_image(resolution=OCR_DPI).original
        content = sink.add_pil(rendered, f"p{page_no:03d}", f"{page_no}쪽", alt, ocr_source=ocr_img)
        if content:
            blocks.append(_Block(top, bottom, "image", content, bbox, ocr=sink.ocr_count > ocr_before))
    return blocks


def _text_between(page, top: float, bottom: float, tables: list[_Block]) -> str:
    if bottom - top < 1:
        return ""
    region = page.crop((0, top, page.width, bottom), strict=False)

    def outside_tables(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(t.bbox[0] <= cx <= t.bbox[2] and t.bbox[1] <= cy <= t.bbox[3] for t in tables)

    return (region.filter(outside_tables).extract_text() or "").strip()


def convert_pdf(pdf_path: str | Path, sink: ImageSink) -> PdfResult:
    chunks: list[str] = []
    n_tables = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        if sink.ocr:
            sink.ocr_lang = ocr_engine.pick_lang("".join(c["text"] for p in pdf.pages[:5] for c in p.chars))
        for page_no, page in enumerate(pdf.pages, 1):
            lines = page.extract_text_lines()
            tables = _page_tables(page)
            images = _page_images(page, page_no, sink, lines)
            if sink.ocr and len(page.chars) < SCANNED_PAGE_MAX_CHARS and not any(b.ocr for b in images):
                text = ocr_engine.ocr_image(page.to_image(resolution=OCR_DPI).original, sink.ocr_lang)
                if text:
                    sink.ocr_count += 1
                    quote = "\n".join(f"> {ln}" if ln else ">" for ln in text.splitlines())
                    images.append(_Block(float(page.height), float(page.height), "image",
                                         f"> [페이지 {page_no} 텍스트 (OCR)]\n>\n{quote}", ocr=True))
            n_tables += len(tables)

            parts: list[str] = []
            cursor = 0.0
            for block in sorted(tables + images, key=lambda b: b.top):
                text = _text_between(page, cursor, block.top, tables)
                if text:
                    parts.append(text)
                parts.append(block.content)
                # Keep text that sits on top of an image (figure labels); skip text inside tables.
                cursor = max(cursor, block.bottom if block.kind == "table" else block.top)
            tail = _text_between(page, cursor, float(page.height), tables)
            if tail:
                parts.append(tail)
            if parts:
                chunks.append("\n\n".join(parts))
            page.close()
    return PdfResult("\n\n".join(chunks).strip(), n_tables)
