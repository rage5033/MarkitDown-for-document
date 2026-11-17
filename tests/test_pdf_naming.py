"""Path/name rules for PDF outputs (markitdown_gui/pdf_naming.py). Run: .venv\\Scripts\\python.exe -m unittest tests.test_pdf_naming"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from markitdown_gui.pdf_naming import (  # noqa: E402
    IMAGE_PREFIX_LEN, MAX_PATH_LEN, MIN_FOLDER_STEM, plan_pdf_output,
)

SHA_A = "a1b2c3" + "0" * 58
SHA_B = "d4e5f6" + "0" * 58
LONG_TITLE = ("Characterisation and performance evaluation of early-generation commercial "
              "sodium-ion batteries for grid storage applications.pdf")  # safe_stem -> 90 chars


def out_dir_of(length: int) -> str:
    d = "D:\\" + "x" * (length - 3)
    assert len(os.path.abspath(d)) == length
    return d


def longest_written(plan, images: bool) -> int:
    """Full length of the longest file names the pipeline actually writes (incl. OCR split bundles)."""
    files = [plan.md, plan.images_pdf, plan.folder / f"{plan.stem}_images_99.pdf"]
    if images:
        files.append(plan.folder / "images" / f"{plan.stem[:IMAGE_PREFIX_LEN]}_p999_999_TableXVIII.jpg")
    return max(len(os.path.abspath(f)) for f in files)


class FolderLayout(unittest.TestCase):
    def test_out_dir_73_keeps_90_char_stem_and_shortens_folder_only(self):
        for images in (True, False):
            p = plan_pdf_output(out_dir_of(73), LONG_TITLE, Path(LONG_TITLE).stem + ".md", SHA_A, images=images)
            self.assertEqual(len(p.stem), 90)
            self.assertIsNone(p.shortened_to)
            self.assertTrue(p.folder.name.endswith("_a1b2c3"))
            self.assertTrue(p.stem.startswith(p.folder.name[:-7]))
            self.assertGreaterEqual(len(p.folder.name) - 7, MIN_FOLDER_STEM)
            self.assertLessEqual(longest_written(p, images), MAX_PATH_LEN)
            self.assertFalse(p.too_long)

    def test_out_dir_150_shortens_stem_once_folder_prefix_hits_minimum(self):
        p = plan_pdf_output(out_dir_of(150), LONG_TITLE, Path(LONG_TITLE).stem + ".md", SHA_A, images=True)
        self.assertEqual(len(p.folder.name), MIN_FOLDER_STEM + 7)
        self.assertEqual(p.shortened_to, len(p.stem))
        self.assertLess(len(p.stem), 90)
        self.assertLessEqual(longest_written(p, True), MAX_PATH_LEN)
        # Without individual image files, the bundle PDF name is the longest file -> stem can stay longer.
        q = plan_pdf_output(out_dir_of(150), LONG_TITLE, Path(LONG_TITLE).stem + ".md", SHA_A, images=False)
        self.assertGreater(len(q.stem), len(p.stem))
        self.assertLessEqual(longest_written(q, False), MAX_PATH_LEN)

    def test_same_source_same_folder_and_different_source_different_folder(self):
        args = (out_dir_of(73), LONG_TITLE, Path(LONG_TITLE).stem + ".md")
        self.assertEqual(plan_pdf_output(*args, SHA_A).folder, plan_pdf_output(*args, SHA_A).folder)
        self.assertNotEqual(plan_pdf_output(*args, SHA_A).folder, plan_pdf_output(*args, SHA_B).folder)

    def test_short_title_is_untouched(self):
        p = plan_pdf_output(out_dir_of(73), "Short paper.pdf", "Short paper.md", SHA_A)
        self.assertEqual((p.stem, p.folder.name), ("Short_paper", "Short_paper_a1b2c3"))
        self.assertFalse(p.user_renamed)


class NamesAndDuplicates(unittest.TestCase):
    def test_user_rename_goes_through_safe_stem(self):
        p = plan_pdf_output(out_dir_of(73), LONG_TITLE, 'my: "paper" (v2).md', SHA_A)
        self.assertTrue(p.user_renamed)
        self.assertEqual(p.cleaned_name, "my_paper_v2")
        self.assertEqual(p.stem, "my_paper_v2")

    def test_other_source_with_same_name_gets_hash_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            first = plan_pdf_output(d, "Paper.pdf", "Paper.md", SHA_A, doc_folder=False)
            first.md.write_text(f'---\nsource_sha256: "{SHA_A}"\n---\n', encoding="utf-8")
            self.assertEqual(plan_pdf_output(d, "Paper.pdf", "Paper.md", SHA_A, doc_folder=False).stem, "Paper")
            other = plan_pdf_output(d, "Paper.pdf", "Paper.md", SHA_B, doc_folder=False)
            self.assertEqual(other.stem, "Paper_d4e5f6")
            self.assertTrue(other.hashed)

    def test_flat_layout_out_dir_150(self):
        p = plan_pdf_output(out_dir_of(150), LONG_TITLE, Path(LONG_TITLE).stem + ".md", SHA_A, doc_folder=False)
        self.assertEqual(p.folder, Path(out_dir_of(150)))
        self.assertLessEqual(longest_written(p, True), MAX_PATH_LEN)


if __name__ == "__main__":
    unittest.main()
