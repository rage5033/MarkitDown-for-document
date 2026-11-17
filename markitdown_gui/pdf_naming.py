"""Output names and folder for the PDF pipeline (CONVERSION_FIX_SPEC 3.1 + long-path rules).

- File stem = safe_stem(output name, 90). Folder (doc-folder layout) = <stem prefix>_<sha256[:6]>, so the
  same source always lands in the same folder and different sources never share one.
- The folder's stem prefix is shortened so the longest file in the folder keeps the full path
  <= MAX_PATH_LEN. Only when that prefix would drop below MIN_FOLDER_STEM is the file stem itself
  shortened (the single exception to the 90-char rule).
- If <stem>.md already exists there for a different source, the stem gets a _<sha6> suffix.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from markitdown_gui.pdf_pipeline import safe_stem

MAX_PATH_LEN = 240        # Explorer/Acrobat fail beyond MAX_PATH (260); keep a margin
STEM_MAX = 90             # spec 3.1
MIN_FOLDER_STEM = 20
MIN_FILE_STEM = 8
IMAGE_PREFIX_LEN = 40     # pdf_pipeline: images/<stem[:40]>_p###_##_<tag>.jpg
BUNDLE_SUFFIX = "_images_99.pdf"         # worst case incl. split bundles of the OCR path
IMAGE_TAIL = len("_p999_999_TableXVIII.jpg")
IMAGES_DIR = "images" + os.sep


@dataclass
class PdfOutputPlan:
    folder: Path
    stem: str
    md: Path
    images_pdf: Path
    longest: int                  # longest full path among the files this conversion can write
    shortened_to: int | None      # file stem length when the 90-char rule had to be broken
    user_renamed: bool            # output name differs from the source file name (after safe_stem)
    cleaned_name: str             # safe_stem(output name) before any shortening/hash suffix
    hashed: bool = False          # _<sha6> suffix added because another source already owns <stem>.md

    @property
    def too_long(self) -> bool:
        return self.longest > MAX_PATH_LEN


def _file_max(stem_len: int, images: bool) -> int:
    longest = stem_len + len(BUNDLE_SUFFIX)
    if images:
        longest = max(longest, len(IMAGES_DIR) + min(stem_len, IMAGE_PREFIX_LEN) + IMAGE_TAIL)
    return longest


def _other_source(md: Path, sha: str) -> bool:
    return md.exists() and sha not in md.read_text(encoding="utf-8", errors="ignore")[:3000]


def plan_pdf_output(out_dir: str | Path, source: str | Path, output_name: str, sha: str,
                    doc_folder: bool = True, images: bool = True) -> PdfOutputPlan:
    out_dir = Path(out_dir)
    out_len = len(os.path.abspath(out_dir))
    tag = sha[:6]
    cleaned = safe_stem(Path(output_name), STEM_MAX)
    renamed = cleaned != safe_stem(Path(source), STEM_MAX)
    stem, shortened = cleaned, None

    if doc_folder:
        def total(folder_len: int, n: int) -> int:
            return out_len + 1 + folder_len + 1 + _file_max(n, images)

        sfx = 1 + len(tag)
        f = min(len(stem), MAX_PATH_LEN - total(sfx, len(stem)))
        if f < min(MIN_FOLDER_STEM, len(stem)):
            f = min(MIN_FOLDER_STEM, len(stem))
            n = len(stem)
            while n > MIN_FILE_STEM and total(f + sfx, n) > MAX_PATH_LEN:
                n -= 1
            stem = stem[:n].rstrip("._ ") or stem[:n]
            shortened = len(stem)
        folder = out_dir / f"{cleaned[:f].rstrip('._ ')}_{tag}"
        n_max = STEM_MAX
        while n_max > MIN_FILE_STEM and total(len(folder.name), n_max) > MAX_PATH_LEN:
            n_max -= 1
    else:
        folder = out_dir

        def total(n: int) -> int:
            return out_len + 1 + _file_max(n, images)

        n_max = STEM_MAX
        while n_max > MIN_FILE_STEM and total(n_max) > MAX_PATH_LEN:
            n_max -= 1
        if n_max < len(stem):
            stem = stem[:n_max].rstrip("._ ") or stem[:n_max]
            shortened = len(stem)

    hashed = _other_source(folder / f"{stem}.md", sha)
    if hashed:
        keep = max(1, min(len(stem), STEM_MAX - 7, n_max - 7))
        stem = f"{stem[:keep].rstrip('._ ')}_{tag}"

    md, images_pdf = folder / f"{stem}.md", folder / f"{stem}_images.pdf"
    longest = len(os.path.abspath(folder)) + 1 + _file_max(len(stem), images)
    return PdfOutputPlan(folder, stem, md, images_pdf, longest, shortened, renamed, cleaned, hashed)


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
