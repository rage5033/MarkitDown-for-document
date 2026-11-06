"""Optional Tesseract OCR; every caller must tolerate Tesseract being absent."""
from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

import pytesseract

_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
)
MIN_OCR_CHARS = 10


@lru_cache(maxsize=1)
def _setup() -> tuple[bool, str, str]:
    """Returns (available, lang, status message)."""
    exe = shutil.which("tesseract") or next((str(p) for p in _CANDIDATES if p.is_file()), None)
    if not exe:
        return False, "", "Tesseract 미설치 (install_tesseract.bat 실행 필요)"
    pytesseract.pytesseract.tesseract_cmd = exe
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        return False, "", f"Tesseract 실행 실패: {exc}"
    lang = "+".join(l for l in ("kor", "eng") if l in langs) or "eng"
    note = "" if "kor" in langs else " (한국어 데이터 없음 - 영어만 인식)"
    return True, lang, f"Tesseract 사용 가능: {lang}{note}"


def is_available() -> bool:
    return _setup()[0]


def status() -> str:
    return _setup()[2]


HANGUL = re.compile(r"[\uac00-\ud7a3]")
MIN_WORD_CONF = 60
SPARSE_GAIN = 1.15
# Diagram borders/arrows come back as these; isolated Hangul jamo are Korean-model misreads of lines.
NOISE_TOKEN = re.compile(r"^([|l!]|[^\w]+|[\u3131-\u318e]+)$")


def pick_lang(document_text: str) -> str:
    """English-only OCR for documents without Korean text avoids Korean misreads of English/graphics."""
    ok, lang, _ = _setup()
    if not ok or not document_text.strip() or HANGUL.search(document_text):
        return lang
    return "eng" if "eng" in lang else lang


def _ocr_pass(image, lang: str, psm: int) -> tuple[str, int]:
    """Returns (text, confident char count) for one page-segmentation mode."""
    data = pytesseract.image_to_data(image, lang=lang, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    words = [
        (data["top"][i], data["left"][i], data["height"][i],
         (data["block_num"][i], data["par_num"][i], data["line_num"][i]), w.strip())
        for i, w in enumerate(data["text"])
        if w.strip() and float(data["conf"][i]) >= MIN_WORD_CONF and not NOISE_TOKEN.match(w.strip())
    ]
    if psm == 11:
        # Sparse mode makes every word its own block; rebuild visual lines from word positions.
        rows: list[list[tuple]] = []
        for w in sorted(words):
            if rows and abs(w[0] - rows[-1][0][0]) < 0.6 * max(w[2], rows[-1][0][2]):
                rows[-1].append(w)
            else:
                rows.append([w])
        text = "\n".join(" ".join(w[4] for w in sorted(r, key=lambda w: w[1])) for r in rows)
    else:
        out: list[str] = []
        prev = None
        for w in words:
            block_par_line = w[3]
            if prev is not None and prev[:2] != block_par_line[:2]:
                out.append("")
            if prev == block_par_line:
                out[-1] += " " + w[4]
            else:
                out.append(w[4])
            prev = block_par_line
        text = "\n".join(out).strip()
    return text, len(re.sub(r"\s", "", text))


def ocr_image(image, lang: str | None = None) -> str:
    """OCR a PIL image keeping only confident words; returns '' when nothing meaningful is recognized."""
    ok, default_lang, _ = _setup()
    if not ok:
        return ""
    gray = image.convert("L")
    lang = lang or default_lang
    # PSM 3 keeps multi-column reading order; PSM 11 recovers text that table borders hide from PSM 3.
    text, chars = _ocr_pass(gray, lang, 3)
    sparse_text, sparse_chars = _ocr_pass(gray, lang, 11)
    if sparse_chars > chars * SPARSE_GAIN:
        text = sparse_text
    return text if len(re.findall(r"\w", text)) >= MIN_OCR_CHARS else ""
