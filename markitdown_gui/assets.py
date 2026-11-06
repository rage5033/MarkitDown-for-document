"""Shared image handling so every format yields AI-upload-friendly files: flat, uniquely named, AI-readable formats."""
from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass, field
from glob import escape as glob_escape
from pathlib import Path

from PIL import Image

from markitdown_gui import ocr as ocr_engine

AI_FORMATS = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
MIN_IMAGE_PX = 40          # icons/bullets below this are kept only as alt text
MAX_PREFIX_LEN = 40        # keeps full paths well under Windows' 260-char limit
BUNDLE_MAX_PAGES = 90      # Claude analyzes PDF visuals only up to 100 pages
# Strictest per-file limits across Claude (Projects: 30MB/file, images ≤8000px) and ChatGPT (512MB/file,
# images 20MB), with a safety margin, so every uploaded file is accepted by both services.
AI_MAX_FILE_BYTES = 28 * 1024 * 1024
AI_MAX_IMAGE_BYTES = 19 * 1024 * 1024
AI_MAX_IMAGE_SIDE = 8000
DATA_URI_IMG = re.compile(r"!\[([^\]]*)\]\(data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)\)")
SLIDE_MARK = re.compile(r"<!-- Slide number: (\d+) -->")
GENERIC_ALT = re.compile(r"^(picture|image|그림|사진)\s*\d*$", re.IGNORECASE)


def safe_prefix(stem: str) -> str:
    # Spaces/brackets/URL-special chars would break Markdown image links.
    return re.sub(r"[\s#?%()\[\]{}]+", "_", stem).strip("_")[:MAX_PREFIX_LEN] or "doc"


def jpeg_within(image: Image.Image, max_bytes: int, quality: int = 92) -> bytes:
    """Encode as JPEG, lowering quality and then resolution until it fits `max_bytes`."""
    image = image.convert("RGB")
    while True:
        buf = io.BytesIO()
        # No optimize=True: Pillow fails on very large images with it ("Suspension not allowed here").
        image.save(buf, "JPEG", quality=quality, subsampling=0 if quality >= 85 else 2)
        if buf.tell() <= max_bytes or min(image.size) < 64:
            return buf.getvalue()
        if quality > 76:
            quality -= 8
        else:
            image = image.resize((max(image.width * 3 // 4, 1), max(image.height * 3 // 4, 1)), Image.LANCZOS)


@dataclass
class ImageSink:
    image_dir: Path | None       # None = don't save files (OCR-only or disabled)
    link_prefix: str             # "" for flat layout, "<x>_images/" for subfolder layout
    name_prefix: str
    ocr: bool = False
    ocr_lang: str | None = None  # set per document via ocr.pick_lang()
    entries: list[tuple[str, str, str]] = field(default_factory=list)  # (file, location, alt)
    ocr_count: int = 0
    _by_hash: dict[str, str] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)
    _counters: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.ocr = self.ocr and ocr_engine.is_available()

    @property
    def enabled(self) -> bool:
        return self.image_dir is not None or self.ocr

    def clear_previous(self, extra_dirs: tuple[Path, ...] = ()) -> None:
        """Remove files a previous run generated for this document, so stale images aren't uploaded."""
        pattern = re.compile(rf"^{re.escape(self.name_prefix)}_(p\d{{3}}|s\d{{2,}}|img)_\d{{2,}}\.\w+$")
        for d in (self.image_dir, *extra_dirs):
            if d is None or not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and pattern.match(f.name):
                    f.unlink()

    def _next_name(self, tag: str, ext: str) -> str:
        n = self._counters.get(tag, 0) + 1
        self._counters[tag] = n
        return f"{self.name_prefix}_{tag}_{n:02d}{ext}"

    def _save_pil(self, image: Image.Image, tag: str) -> str:
        """Save as PNG, falling back to JPEG / downscaling so the file meets AI per-image limits."""
        if max(image.size) > AI_MAX_IMAGE_SIDE:
            image = image.copy()
            image.thumbnail((AI_MAX_IMAGE_SIDE, AI_MAX_IMAGE_SIDE), Image.LANCZOS)
        if "A" in image.getbands() or image.mode == "P":
            image = image.convert("RGBA")
        elif image.mode != "RGB":
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, "PNG")
        data, ext = buf.getvalue(), ".png"
        if len(data) > AI_MAX_IMAGE_BYTES:
            data, ext = jpeg_within(image, AI_MAX_IMAGE_BYTES), ".jpg"
        name = self._next_name(tag, ext)
        (self.image_dir / name).write_bytes(data)
        return name

    def add_pil(self, image: Image.Image, tag: str, location: str, alt: str, ocr_source: Image.Image | None = None) -> str:
        """Save a rendered image (PDF path); returns the Markdown to insert."""
        parts = []
        if self.image_dir is not None:
            self.image_dir.mkdir(parents=True, exist_ok=True)
            name = self._save_pil(image, tag)
            self.entries.append((name, location, alt))
            parts.append(f"![{alt}]({self.link_prefix}{name})")
        return self._with_ocr(parts, ocr_source or image)

    def add_bytes(self, data: bytes, content_type: str, tag: str, location: str, alt: str) -> str:
        """Save an embedded image (Office/HTML path), converting non-AI formats to PNG."""
        try:
            pil = Image.open(io.BytesIO(data))
            pil.load()
        except Exception:
            pil = None
        if pil is not None and min(pil.size) < MIN_IMAGE_PX:
            return ""
        digest = hashlib.sha1(data).hexdigest()
        repeat = digest in self._seen
        self._seen.add(digest)
        parts = []
        if self.image_dir is not None:
            name = self._by_hash.get(digest)
            if name is None:
                ext = AI_FORMATS.get(content_type.lower())
                self.image_dir.mkdir(parents=True, exist_ok=True)
                within_limits = len(data) <= AI_MAX_IMAGE_BYTES and (pil is None or max(pil.size) <= AI_MAX_IMAGE_SIDE)
                if ext and within_limits:
                    name = self._next_name(tag, ext)
                    (self.image_dir / name).write_bytes(data)
                elif pil is not None:
                    name = self._save_pil(pil, tag)
                else:
                    ext = "." + content_type.split("/")[-1].replace("x-", "").replace("+xml", "")
                    name = self._next_name(tag, ext)
                    (self.image_dir / name).write_bytes(data)
                    alt = f"{alt} (AI 미지원 형식)"
                self._by_hash[digest] = name
                self.entries.append((name, location, alt))
            parts.append(f"![{alt}]({self.link_prefix}{name})")
        return self._with_ocr(parts, None if repeat else pil) or alt

    def _with_ocr(self, parts: list[str], image: Image.Image | None) -> str:
        if self.ocr and image is not None:
            text = ocr_engine.ocr_image(image, self.ocr_lang)
            if text:
                self.ocr_count += 1
                body = "\n".join(f"> {ln}" if ln else ">" for ln in text.splitlines())
                parts.append(f"> [이미지 내 텍스트 (OCR)]\n>\n{body}")
        return "\n\n".join(parts)

    def index_markdown(self, bundles: list[tuple[str, int]] | None = None) -> str:
        """`bundles` = [(bundle pdf name, images in it)] when an image-bundle PDF was written."""
        if not self.entries:
            return ""
        if not bundles:
            rows = "\n".join(f"| {f} | {loc} | {alt.replace('|', '/')} |" for f, loc, alt in self.entries)
            return (
                "\n\n---\n\n## 이미지 파일 목록\n\n"
                "이 문서의 이미지는 아래 파일명으로 함께 제공됩니다. 본문의 이미지 링크와 파일명이 일치합니다.\n\n"
                "| 파일명 | 위치 | 설명 |\n|---|---|---|\n" + rows + "\n"
            )
        where = []
        for name, count in bundles:
            where += [(name, page) for page in range(1, count + 1)]
        rows = "\n".join(
            f"| {f} | {b} {p}쪽 | {loc} | {alt.replace('|', '/')} |" for (f, loc, alt), (b, p) in zip(self.entries, where)
        )
        names = ", ".join(f"'{b}'" for b, _ in bundles)
        return (
            "\n\n---\n\n## 이미지 파일 목록\n\n"
            f"이 문서의 이미지는 이미지 모음 PDF({names})에 한 쪽에 1개씩 들어 있습니다. "
            "각 쪽 상단에 본문 이미지 링크와 같은 파일명이 표시되어 있습니다.\n\n"
            "| 파일명 | 이미지 모음 PDF | 원문 위치 | 설명 |\n|---|---|---|---|\n" + rows + "\n"
        )

    @staticmethod
    def remove_bundles(base: Path) -> None:
        for old in base.parent.glob(f"{glob_escape(base.name)}*.pdf"):
            if re.fullmatch(rf"{re.escape(base.name)}(_\d+)?\.pdf", old.name):
                old.unlink()

    def write_bundle(self, base: Path) -> list[tuple[str, int]]:
        """Write all saved images into `<base>.pdf`, split as `<base>_2.pdf`, ... so each file stays within
        BUNDLE_MAX_PAGES and AI_MAX_FILE_BYTES; returns [(name, count)]."""
        self.remove_bundles(base)
        if self.image_dir is None or not self.entries:
            return []
        from markitdown_gui.bundle_pdf import encode_image, estimate_size, write_image_pdf

        per_image = AI_MAX_FILE_BYTES - estimate_size([b""])
        items = [(encode_image(self.image_dir / f, per_image), f, loc, alt) for f, loc, alt in self.entries]
        chunks: list[list] = [[]]
        for item in items:
            chunk = chunks[-1]
            if chunk and (len(chunk) >= BUNDLE_MAX_PAGES
                          or estimate_size([d for d, *_ in chunk] + [item[0]]) > AI_MAX_FILE_BYTES):
                chunks.append([])
            chunks[-1].append(item)

        written: list[tuple[str, int]] = []
        start = 1
        for n, chunk in enumerate(chunks, 1):
            path = base.with_name(base.name + (f"_{n}" if n > 1 else "") + ".pdf")
            write_image_pdf(path, chunk, title=base.name, start=start, total=len(items))
            written.append((path.name, len(chunk)))
            start += len(chunk)
        return written


def extract_data_uri_images(markdown: str, sink: ImageSink) -> str:
    """Replace inline base64 images (MarkItDown keep_data_uris output) with saved files, in document order."""
    out: list[str] = []
    pos = 0
    slide = 0
    for m in re.finditer(f"{SLIDE_MARK.pattern}|{DATA_URI_IMG.pattern}", markdown):
        out.append(markdown[pos:m.start()])
        pos = m.end()
        if m.group(1):
            slide = int(m.group(1))
            out.append(m.group(0))
            continue
        alt, ctype, b64 = m.group(2) or "", m.group(3), m.group(4)
        location = f"슬라이드 {slide}" if slide else "본문"
        tag = f"s{slide:02d}" if slide else "img"
        alt = alt.strip()
        if not alt or GENERIC_ALT.match(alt):
            alt = f"Image ({location})"
        if not sink.enabled:
            out.append(f"[{alt}]")
            continue
        try:
            data = base64.b64decode(b64)
        except Exception:
            out.append(f"[{alt}]")
            continue
        out.append(sink.add_bytes(data, ctype, tag, location, alt))
    out.append(markdown[pos:])
    return "".join(out)
