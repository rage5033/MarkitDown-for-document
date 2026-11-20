"""Caption rules (T7): Chinese captions and bilingual merge. Run: .venv\\Scripts\\python.exe -m unittest tests.test_captions"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from markitdown_gui import pdf_pipeline as P  # noqa: E402
import verify_conversion as V  # noqa: E402

CASES = {
    "图1": "Fig 1", "图1 钠离子电池": "Fig 1", "图１\u3000结构": "Fig 1", "图3-2 曲线": "Fig 3-2",
    "图3.2：示意": "Fig 3.2", "图3.2．示意": "Fig 3.2", "表1": "Table 1", "表 2 参数": "Table 2",
    "圖4: 架構": "Fig 4", "如图1所示": None, "图1为系统": None, "由表2可知": None, "图12所示的": None,
    "Fig. 3. Topology": "Fig 3", "Table II": "Table II", "Figure 5 shows": None,
}


class CaptionLabels(unittest.TestCase):
    def test_pipeline_and_verifier_agree_on_all_cases(self):
        for text, want in CASES.items():
            with self.subTest(text=text):
                self.assertEqual(P.label_of(text), want)
                self.assertEqual(V.label_of(text), want)


class BilingualMerge(unittest.TestCase):
    @staticmethod
    def cap(label, text, y0, y1):
        return dict(label=label, text=text, rect=pymupdf.Rect(100, y0, 300, y1))

    def test_adjacent_zh_and_en_same_number_become_one_caption(self):
        caps = P._merge_bilingual_captions([self.cap("Fig 1", "图 1 结构", 500, 508),
                                            self.cap("Fig 1", "Fig. 1 Structure", 511, 519)])
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["text"], "图 1 结构 Fig. 1 Structure")
        self.assertEqual((caps[0]["rect"].y0, caps[0]["rect"].y1), (500, 519))

    def test_english_only_and_far_apart_captions_are_untouched(self):
        en = [self.cap("Fig 1", "Fig. 1. A", 500, 508), self.cap("Fig 1", "Fig. 1. B", 511, 519)]
        self.assertEqual(len(P._merge_bilingual_captions(en)), 2)
        far = [self.cap("Fig 1", "图 1 结构", 100, 108), self.cap("Fig 1", "Fig. 1 Structure", 400, 408)]
        self.assertEqual(len(P._merge_bilingual_captions(far)), 2)


class CjkBodyBlocks(unittest.TestCase):
    def test_han_lines_use_20_chars_and_english_lines_keep_40(self):
        self.assertTrue(V.is_body_block(["钠离子电池作为一种新型的电化学储能技术具有", "资源丰富安全性高高低温性能优异高倍率充放"]))
        self.assertFalse(V.is_body_block(["a" * 30, "b" * 30]))
        self.assertTrue(V.is_body_block(["a" * 40, "b" * 40]))


if __name__ == "__main__":
    unittest.main()
