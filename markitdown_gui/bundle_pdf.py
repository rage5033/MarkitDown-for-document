"""One image per A4 page with a label header, so an AI can match each page to the MD's image links."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

MARGIN = 36
HEADER_H = 54
JPEG_QUALITY = 92
PAGE_OVERHEAD = 4 * 1024        # header text + page objects, per page
FILE_OVERHEAD = 512 * 1024      # embedded font subset + document structure
_FONT = "Helvetica"


def _font() -> str:
    global _FONT
    if _FONT == "Helvetica":
        malgun = Path(r"C:\Windows\Fonts\malgun.ttf")
        if malgun.is_file():
            pdfmetrics.registerFont(TTFont("Malgun", str(malgun)))
            _FONT = "Malgun"
    return _FONT


def _fit(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> str:
    while text and c.stringWidth(text, font, size) > width:
        text = text[:-2] + "…"
    return text


def encode_image(img_path: Path, max_bytes: int) -> bytes | None:
    """JPEG bytes to embed (None if unreadable). ReportLab stores PNGs as raw zlib data (larger than
    the PNG itself); high-quality 4:4:4 JPEG keeps figures sharp at a fraction of the size.
    Originals stay lossless on disk."""
    from markitdown_gui.assets import jpeg_within

    try:
        with Image.open(img_path) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            return jpeg_within(im, max_bytes, JPEG_QUALITY)
    except Exception:
        return None


def estimate_size(data_items: list[bytes | None]) -> int:
    return FILE_OVERHEAD + sum(PAGE_OVERHEAD + len(d or b"") for d in data_items)


def write_image_pdf(
    path: Path, items: list[tuple[bytes | None, str, str, str]], title: str, start: int = 1, total: int | None = None,
) -> None:
    """`items` = [(JPEG bytes from encode_image, file name, source location, caption)].
    `start`/`total` number pages across split bundles (e.g. [91/120])."""
    font = _font()
    page_w, page_h = A4
    usable_w = page_w - 2 * MARGIN
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"{title} - 이미지 모음")
    total = total or len(items)
    for i, (data, name, location, caption) in enumerate(items, start):
        top = page_h - MARGIN
        c.setFont(font, 11)
        c.drawString(MARGIN, top - 12, _fit(c, f"[{i}/{total}] {name}", font, 11, usable_w))
        c.setFont(font, 9)
        c.drawString(MARGIN, top - 28, _fit(c, f"원문 위치: {location}", font, 9, usable_w))
        c.drawString(MARGIN, top - 42, _fit(c, f"설명: {caption}", font, 9, usable_w))
        c.line(MARGIN, top - HEADER_H + 4, page_w - MARGIN, top - HEADER_H + 4)
        try:
            if data is None:
                raise ValueError
            img = ImageReader(io.BytesIO(data))
            iw, ih = img.getSize()
            box_w, box_h = usable_w, page_h - 2 * MARGIN - HEADER_H
            scale = min(box_w / iw, box_h / ih, 2.0)
            w, h = iw * scale, ih * scale
            c.drawImage(img, MARGIN + (box_w - w) / 2, top - HEADER_H - h, w, h, preserveAspectRatio=True)
        except Exception:
            c.drawString(MARGIN, top - HEADER_H - 20, "(이 형식의 이미지는 PDF에 넣을 수 없습니다 - 개별 파일을 확인하세요)")
        c.showPage()
    c.save()
