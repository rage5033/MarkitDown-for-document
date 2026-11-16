"""Background conversion worker so MarkItDown calls never block the GUI thread."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from glob import escape as glob_escape
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from markitdown import MarkItDown

from markitdown_gui import ocr as ocr_engine
from markitdown_gui.assets import (
    AI_MAX_FILE_BYTES, MAX_PREFIX_LEN, DATA_URI_IMG, ImageSink, extract_data_uri_images, safe_prefix,
)
from markitdown_gui.pdf_enhanced import convert_pdf
from markitdown_gui.pdf_export import markdown_to_pdf

LAYOUT_DOC_FOLDER = "doc_folder"  # out/<name>/<name>.md + images flat beside it (AI upload: Ctrl+A in one folder)
LAYOUT_SUBFOLDER = "subfolder"    # out/<name>.md + out/<name>_images/
AI_UPLOAD_WARN_FILES = 10
# MarkItDown drops images inside these tags unless told to keep them (e.g. Word images in table cells).
KEEP_INLINE_IMAGES_IN = ["td", "th", "li", "a", "span", "strong", "em", "b", "i", "p"]
MAX_OUTPUT_PATH = 250  # Windows MAX_PATH is 260 incl. terminator; keep a margin
MIN_STEM_LEN = 16
_BUNDLE_SUFFIX = "_images_99.pdf"
_IMAGE_SUFFIX = "_p999_99.png"
# Scanned-PDF detection: a page with fewer non-space text-layer characters than SCAN_PAGE_MIN_CHARS has
# (almost) no text; the PDF counts as scanned when more than SCAN_PAGE_RATIO of its pages are like that.
SCAN_PAGE_MIN_CHARS = 50
SCAN_PAGE_RATIO = 0.5


def scan_low_text_pages(pdf_path: str | Path) -> tuple[list[int], int]:
    """(1-based numbers of pages with almost no text layer, page count)."""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        low = [p.number + 1 for p in doc if len("".join(p.get_text().split())) < SCAN_PAGE_MIN_CHARS]
        return low, doc.page_count


def _page_list(pages: list[int], limit: int = 20) -> str:
    text = ", ".join(map(str, pages[:limit]))
    return text + (f" 외 {len(pages) - limit}쪽" if len(pages) > limit else "")


def _longest_output_path(out_len: int, stem_len: int, layout: str) -> int:
    prefix_len = min(stem_len, MAX_PREFIX_LEN)
    if layout == LAYOUT_DOC_FOLDER:
        # <out>\<stem>\<stem>_images_99.pdf  and  <out>\<stem>\images\<prefix>_p999_99.png
        return out_len + 1 + stem_len + 1 + max(stem_len + len(_BUNDLE_SUFFIX), len("images\\") + prefix_len + len(_IMAGE_SUFFIX))
    # <out>\<stem>_images_99.pdf  and  <out>\<prefix>_images\<prefix>_p999_99.png
    return out_len + 1 + max(stem_len + len(_BUNDLE_SUFFIX), 2 * prefix_len + len("_images\\") + len(_IMAGE_SUFFIX))


def fit_stem(out_dir: Path, stem: str, layout: str) -> str:
    """Shorten `stem` so every generated file stays under Windows' MAX_PATH; most viewers
    (Explorer, Acrobat, Edge) can't open longer paths even when Python could write them."""
    out_len = len(os.path.abspath(out_dir))
    if _longest_output_path(out_len, len(stem), layout) <= MAX_OUTPUT_PATH:
        return stem
    n = len(stem)
    while n > MIN_STEM_LEN and _longest_output_path(out_len, n, layout) > MAX_OUTPUT_PATH:
        n -= 1
    # Hash of the full name keeps truncated names unique and stable across runs.
    tag = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:6]
    head = stem[:max(n - len(tag) - 1, 1)].rstrip(" ._-")
    return f"{head}_{tag}"


def output_paths(out_dir: Path, md_name: str, layout: str, bundle: bool = False) -> tuple[Path, Path, str, Path]:
    """Returns (md_path, image_dir, link_prefix, bundle_pdf_base without .pdf)."""
    stem = fit_stem(out_dir, Path(md_name).stem, layout)
    md_name = stem + Path(md_name).suffix
    if layout == LAYOUT_DOC_FOLDER:
        folder = out_dir / stem
        bundle_base = folder / f"{stem}_images"
        if bundle:
            # Keep only MD + bundle PDF at the top so the upload set is obvious; PNGs stay for human viewing.
            return folder / md_name, folder / "images", "images/", bundle_base
        return folder / md_name, folder, "", bundle_base
    image_dir = out_dir / f"{safe_prefix(stem)}_images"
    return out_dir / md_name, image_dir, f"{image_dir.name}/", out_dir / f"{stem}_images"


@dataclass
class ConversionJob:
    row: int
    source_path: str
    output_name: str  # filename only, must end with .md
    save_pdf: bool = False
    extract_images: bool = True
    ocr: bool = False
    layout: str = LAYOUT_DOC_FOLDER
    bundle_pdf: bool = False


class ConversionWorker(QObject):
    # row, output_path, success, message
    progress = Signal(int, str, bool, str)
    finished = Signal()

    def __init__(self, jobs: list[ConversionJob], output_dir: str):
        super().__init__()
        self._jobs = jobs
        self._output_dir = Path(output_dir)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        md = MarkItDown(enable_plugins=False)
        for job in self._jobs:
            row = job.row
            if self._cancelled:
                break
            bundle = job.bundle_pdf and job.extract_images
            out_path, image_dir, link_prefix, bundle_base = output_paths(
                self._output_dir, job.output_name, job.layout, bundle
            )
            if Path(job.source_path).suffix.lower() == ".pdf":
                try:
                    pdf_out, message = self._run_pdf(job, out_path.parent)
                    self.progress.emit(row, str(pdf_out), True, message)
                except Exception as exc:
                    self.progress.emit(row, str(out_path), False, str(exc))
                continue
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                sink = ImageSink(
                    image_dir if job.extract_images else None, link_prefix,
                    safe_prefix(out_path.stem), ocr=job.ocr,
                )
                sink.clear_previous((out_path.parent, out_path.parent / "images"))
                markdown, message = self._convert(md, job, sink)
                bundles = sink.write_bundle(bundle_base) if bundle else []
                if not bundle:
                    sink.remove_bundles(bundle_base)
                leftover = out_path.parent / "images"
                if leftover.is_dir() and not any(leftover.iterdir()):
                    leftover.rmdir()
                markdown = markdown.rstrip() + sink.index_markdown(bundles)
                out_path.write_text(markdown, encoding="utf-8")

                if bundles:
                    n_files = 1 + len(bundles)
                    message += f" · AI 업로드: {out_path.name} + {', '.join(b for b, _ in bundles)} ({n_files}개)"
                else:
                    n_files = 1 + len(sink.entries)
                    message += f" · AI 업로드 파일 {n_files}개"
                if n_files > AI_UPLOAD_WARN_FILES:
                    message += " (AI 서비스의 1회 업로드 개수 제한에 걸릴 수 있음)"
                if job.save_pdf:
                    try:
                        markdown_to_pdf(markdown, out_path.with_suffix(".pdf"))
                        message += " + PDF"
                    except Exception as pdf_exc:
                        message += f", PDF 실패: {pdf_exc}"
                if out_path.name != job.output_name:
                    message += f" · 경로가 길어 이름을 '{out_path.stem}'(으)로 줄임"
                oversized = self._oversized_outputs(out_path, image_dir, bundle_base, sink, job.save_pdf)
                if oversized:
                    limit_mb = AI_MAX_FILE_BYTES // (1024 * 1024)
                    message += f" · 경고: {limit_mb}MB 초과 파일 {', '.join(oversized)} (Claude 프로젝트 업로드 불가)"
                self.progress.emit(row, str(out_path), True, message)
            except Exception as exc:  # surface any converter error to the UI instead of crashing the thread
                self.progress.emit(row, str(out_path), False, str(exc))
        self.finished.emit()

    @staticmethod
    def _run_pdf(job: ConversionJob, target_dir: Path) -> tuple[Path, str]:
        """PDF goes through the layout pipeline, which writes the final MD and image-bundle PDF itself;
        none of the ImageSink post-processing below may touch its output. Only scanned PDFs with OCR
        enabled (and Tesseract installed) take the legacy pdfplumber + Tesseract path."""
        from markitdown_gui import pdf_pipeline

        low_pages, total = scan_low_text_pages(job.source_path)
        scanned = len(low_pages) > SCAN_PAGE_RATIO * total
        if scanned and job.ocr and ocr_engine.is_available():
            return ConversionWorker._run_pdf_ocr(job, target_dir, total)

        res = pdf_pipeline.convert_pdf(job.source_path, target_dir, save_image_files=job.extract_images)
        rep, md_path = res["report"], Path(res["md"])
        bundle = [Path(res["images_pdf"])] if res["images_pdf"] else []
        message = f"완료 (그림 {rep['figures']}개, 표 {rep['tables']}개, 수식 이미지 {rep['formulas']}개)"
        if bundle:
            message += f" · AI 업로드: {md_path.name} + {bundle[0].name} (2개)"
        else:
            message += f" · AI 업로드: {md_path.name} (1개, 이미지 없음)"
        if scanned:
            hint = "OCR을 켜고" if ocr_engine.is_available() else "Tesseract를 설치하고 OCR을 켜서"
            message += f" · 경고: 스캔 PDF로 보임 — {hint} 다시 변환하세요"
        elif low_pages:
            message += f" · 글자가 거의 없는 쪽: {_page_list(low_pages)}"
        return md_path, ConversionWorker._finish_pdf(job, md_path, bundle, message)

    @staticmethod
    def _run_pdf_ocr(job: ConversionJob, target_dir: Path, pages: int) -> tuple[Path, str]:
        from markitdown_gui import pdf_pipeline
        from markitdown_gui.pdf_enhanced import convert_pdf as convert_scanned_pdf

        src = Path(job.source_path)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        target_dir.mkdir(parents=True, exist_ok=True)
        stem, md_path, img_pdf_path = pdf_pipeline.output_names(src, target_dir, sha)
        image_dir = target_dir / "images"
        sink = ImageSink(image_dir if job.extract_images else None, "images/", safe_prefix(stem), ocr=True)
        sink.clear_previous((target_dir, image_dir))
        res = convert_scanned_pdf(src, sink)
        bundle_base = img_pdf_path.with_name(stem + "_images")
        bundles = sink.write_bundle(bundle_base) if job.extract_images else []
        if not bundles:
            sink.remove_bundles(bundle_base)
        front = {
            "source_file": src.name, "source_sha256": sha, "pages": pages,
            "images_pdf": bundles[0][0] if bundles else "", "images": len(sink.entries),
            "converter": "scanned PDF OCR / pdfplumber + tesseract",
            "converted_at": datetime.now().isoformat(timespec="seconds"),
        }
        header = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()) + "\n---\n\n"
        md_path.write_text(header + res.markdown.rstrip() + sink.index_markdown(bundles) + "\n",
                           encoding="utf-8", newline="\n")
        message = f"완료 (스캔 PDF → OCR {sink.ocr_count}건, 이미지 {len(sink.entries)}개)"
        if bundles:
            message += f" · AI 업로드: {md_path.name} + {', '.join(b for b, _ in bundles)} ({1 + len(bundles)}개)"
        extra = [img_pdf_path.with_name(b) for b, _ in bundles]
        return md_path, ConversionWorker._finish_pdf(job, md_path, extra, message)

    @staticmethod
    def _finish_pdf(job: ConversionJob, md_path: Path, extra_files: list[Path], message: str) -> str:
        if job.save_pdf:
            try:
                markdown_to_pdf(md_path.read_text(encoding="utf-8"), md_path.with_suffix(".pdf"))
                message += " + PDF"
            except Exception as pdf_exc:
                message += f", PDF 실패: {pdf_exc}"
        files = [md_path, *extra_files, md_path.with_suffix(".pdf")]
        oversized = [f.name for f in files if f.is_file() and f.stat().st_size > AI_MAX_FILE_BYTES]
        if oversized:
            message += f" · 경고: {AI_MAX_FILE_BYTES // (1024 * 1024)}MB 초과 파일 {', '.join(oversized)}"
        return message

    @staticmethod
    def _oversized_outputs(out_path: Path, image_dir: Path, bundle_base: Path, sink: ImageSink, pdf: bool) -> list[str]:
        files = [out_path, *bundle_base.parent.glob(f"{glob_escape(bundle_base.name)}*.pdf")]
        files += [image_dir / f for f, _, _ in sink.entries]
        if pdf:
            files.append(out_path.with_suffix(".pdf"))
        return [f.name for f in files if f.is_file() and f.stat().st_size > AI_MAX_FILE_BYTES]

    @staticmethod
    def _convert(md: MarkItDown, job: ConversionJob, sink: ImageSink) -> tuple[str, str]:
        def summary(extra: str = "") -> str:
            ocr_note = f", OCR {sink.ocr_count}건" if sink.ocr else ""
            return f"완료 ({extra}이미지 {len(sink.entries)}개{ocr_note})"

        if Path(job.source_path).suffix.lower() == ".pdf":
            try:
                res = convert_pdf(job.source_path, sink)
                if res.markdown.strip():
                    return res.markdown, summary(f"표 {res.tables}개, ")
                note = "텍스트 없음 - 스캔 PDF일 수 있음"
            except Exception as exc:
                note = f"기본 변환으로 대체: {exc}"
            return md.convert(job.source_path).markdown, f"완료 ({note})"

        result = md.convert(job.source_path, keep_data_uris=True, keep_inline_images_in=KEEP_INLINE_IMAGES_IN)
        if sink.ocr:
            sink.ocr_lang = ocr_engine.pick_lang(DATA_URI_IMG.sub("", result.markdown))
        return extract_data_uri_images(result.markdown, sink), summary()
