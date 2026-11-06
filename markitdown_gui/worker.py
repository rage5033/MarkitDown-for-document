"""Background conversion worker so MarkItDown calls never block the GUI thread."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
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
