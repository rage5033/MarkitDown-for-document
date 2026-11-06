"""Render Markdown text to PDF with Korean-capable font support (Windows)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import unquote

import markdown as md_lib
import xhtml2pdf.files as _pisa_files
from xhtml2pdf import pisa

_WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
_PATCHED = False


def _patch_windows_tempfile_lock() -> None:
    """xhtml2pdf loads @font-face files via delete=True temp files, which Windows
    refuses to reopen (PermissionError) while the handle is still alive. Switch
    to delete=False so TTFont can read it back."""
    global _PATCHED
    if _PATCHED:
        return

    def _get_named_tmp_file(self):
        data = self.get_data()
        tmp_file = tempfile.NamedTemporaryFile(suffix=self.suffix, delete=False)
        _pisa_files.files_tmp.append(tmp_file)
        if data:
            tmp_file.write(data)
            tmp_file.flush()
        if self.path is None:
            self.path = tmp_file.name
        return tmp_file

    _pisa_files.BaseFile.get_named_tmp_file = _get_named_tmp_file
    _PATCHED = True


def _korean_font_css() -> str:
    regular = _WINDOWS_FONTS / "malgun.ttf"
    bold = _WINDOWS_FONTS / "malgunbd.ttf"
    if not regular.is_file():
        return ""
    bold_rule = (
        f'@font-face {{ font-family: "AppFont"; src: url("{bold.as_posix()}"); font-weight: bold; }}\n'
        if bold.is_file()
        else ""
    )
    return (
        f'@font-face {{ font-family: "AppFont"; src: url("{regular.as_posix()}"); }}\n'
        f"{bold_rule}"
        '* { font-family: "AppFont"; }'
    )


def markdown_to_pdf(markdown_text: str, output_path: str | Path) -> None:
    """Convert `markdown_text` into a PDF file at `output_path`."""
    _patch_windows_tempfile_lock()

    body_html = md_lib.markdown(markdown_text, extensions=["tables", "fenced_code"])
    table_css = "th, td { border: 0.5px solid #888; padding: 2px; }"
    html = f"<html><head><style>{_korean_font_css()}\n{table_css}</style></head><body>{body_html}</body></html>"
    base_dir = Path(output_path).resolve().parent

    def resolve_link(uri: str, rel: str) -> str:
        if "://" in uri or uri.startswith("data:"):
            return uri
        return str(base_dir / unquote(uri))

    with Path(output_path).open("wb") as f:
        result = pisa.CreatePDF(html, dest=f, link_callback=resolve_link)
    if result.err:
        raise RuntimeError("PDF 생성 중 오류가 발생했습니다.")
