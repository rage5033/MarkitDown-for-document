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


class SplitMergedCaptions(unittest.TestCase):
    @staticmethod
    def page_with(draw_below: bool):
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        page.insert_font(fontname="cjk", fontbuffer=pymupdf.Font("cjk").buffer)
        page.draw_rect(pymupdf.Rect(100, 100, 300, 250), color=(0, 0, 0))          # 그림(벡터)
        page.insert_text((110, 270), "图 1 系统结构", fontname="cjk", fontsize=9)
        page.insert_text((110, 282), "Fig. 1 System structure", fontsize=9)
        if draw_below:
            page.draw_line((100, 295), (300, 295))
        return doc, page

    def test_caption_inside_picture_is_split_when_nothing_below(self):
        doc, page = self.page_with(draw_below=False)
        caps, pool = [], [dict(rect=pymupdf.Rect(95, 95, 305, 290), cls="picture", used=False)]
        P._split_merged_captions(page, caps, pool)
        self.assertEqual([c["label"] for c in caps], ["Fig 1"])
        self.assertTrue(caps[0]["split"])
        self.assertIn("System structure", caps[0]["text"])
        self.assertLess(pool[0]["rect"].y1, 262)

    def test_no_split_when_lines_below_caption(self):
        doc, page = self.page_with(draw_below=True)
        caps, pool = [], [dict(rect=pymupdf.Rect(95, 95, 305, 300), cls="picture", used=False)]
        P._split_merged_captions(page, caps, pool)
        self.assertEqual(caps, [])

    def test_no_split_when_label_already_has_a_caption(self):
        doc, page = self.page_with(draw_below=False)
        caps = [dict(label="Fig 1", text="Fig. 1. x", rect=pymupdf.Rect(100, 300, 300, 310))]
        pool = [dict(rect=pymupdf.Rect(95, 95, 305, 290), cls="picture", used=False)]
        P._split_merged_captions(page, caps, pool)
        self.assertEqual(len(caps), 1)

    def test_table_box_with_second_table_caption_becomes_two_tables(self):
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        page.insert_font(fontname="cjk", fontbuffer=pymupdf.Font("cjk").buffer)
        page.insert_text((110, 110), "项目 结果", fontname="cjk", fontsize=9)
        page.insert_text((110, 140), "表 6 系统并网试验结果", fontname="cjk", fontsize=9)
        page.insert_text((110, 150), "Tab. 6 Grid test results", fontsize=9)
        page.insert_text((110, 175), "项目 结果", fontname="cjk", fontsize=9)
        caps = [dict(label="Table 5", text="表 5 静态", rect=pymupdf.Rect(100, 80, 300, 90))]
        pool = [dict(rect=pymupdf.Rect(95, 95, 400, 190), cls="table", used=False)]
        P._split_merged_captions(page, caps, pool)
        self.assertEqual([c["label"] for c in caps], ["Table 5", "Table 6"])
        self.assertIn("Tab. 6", caps[1]["text"])
        self.assertEqual(len(pool), 2)
        self.assertEqual(pool[0]["mixed_with"], "Table 6")
        self.assertLess(pool[0]["rect"].y1, pool[1]["rect"].y0)


class SplitCaptionMarkdown(unittest.TestCase):
    def test_caption_replaces_picture_text_block(self):
        it = type("It", (), dict(caption="图1 钠离子电池 Fig. 1 Temp"))()
        md = "前文\n\n<!-- Start of picture text -->\n20 40 图1 钠离子电池 Fig. 1 Temp\n<!-- End of picture text -->\n后文"
        out = P._restore_split_captions(md, [it])
        self.assertNotIn("Start of picture text", P.PICTURE_TEXT.sub("", out))
        self.assertIn("\n图1 钠离子电池 Fig. 1 Temp\n", out)
        self.assertLess(out.index("前文"), out.index("图1"), out.index("后文"))

    def test_note_before_mixed_text_table(self):
        it = type("It", (), dict(label="Table 5", mixed_with="Table 6"))()
        md = "###### 表 5 静态\n\n|项目|A|\n|---|---|\n|结果|满足|\n|||表6|系统并网|试验结果|\n|项目|B|\n\n后文"
        out = P._mark_mixed_table(md, it)
        self.assertIn("※ 표 텍스트에 表6 내용이 섞여 있음, 이미지 모음 PDF 참조\n\n|项目|A|", out)
        clean = "###### 表 5 静态\n\n|项目|A|\n|---|---|\n|结果|满足|\n\n后文"
        self.assertEqual(P._mark_mixed_table(clean, it), clean)


class CjkBodyBlocks(unittest.TestCase):
    def test_han_lines_use_20_chars_and_english_lines_keep_40(self):
        self.assertTrue(V.is_body_block(["钠离子电池作为一种新型的电化学储能技术具有", "资源丰富安全性高高低温性能优异高倍率充放"]))
        self.assertFalse(V.is_body_block(["a" * 30, "b" * 30]))
        self.assertTrue(V.is_body_block(["a" * 40, "b" * 40]))


if __name__ == "__main__":
    unittest.main()
