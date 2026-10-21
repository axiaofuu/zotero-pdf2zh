"""Offline regressions exercising the real BabelDOC output parser and styles."""
import re
import unittest

from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraph, PdfStyle, PdfSameStyleCharacters
from babeldoc.format.pdf.document_il.midend.il_translator import (
    ILTranslator, RichTextPlaceholder, FormulaPlaceholder, ParagraphTranslateTracker,
)
from babeldoc.translator.translator import OpenAITranslator


class PlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.translator = object.__new__(ILTranslator)
        self.translator._formula_placeholder_pattern = re.compile(r"\{\s*v\s*\d+\s*\}", re.I)
        self.translator._style_left_placeholder_pattern = re.compile(r"<\s*style\s*id\s*=\s*'\s*\d+\s*'\s*>", re.I)
        self.translator._style_right_placeholder_pattern = re.compile(r"<\s*/\s*style\s*>", re.I)
        self.base = PdfStyle(font_id="regular", font_size=10)
        self.italic = PdfStyle(font_id="italic", font_size=10)
        self.formula = PdfFormula()
        self.placeholders = []
        for ident in (1, 3, 6, 9):
            left, lp = OpenAITranslator.get_rich_text_left_placeholder(None, ident)
            right, rp = OpenAITranslator.get_rich_text_right_placeholder(None, ident)
            self.placeholders.append(RichTextPlaceholder(ident, PdfSameStyleCharacters(pdf_style=self.italic), left, right, lp, rp))
        self.placeholders.append(FormulaPlaceholder(2, self.formula, "{v2}", r"\{\s*v\s*2\s*\}"))
        self.source = "Traditionally <style id='1'>policy</style> <style id='3'>state</style> {v2} <style id='6'>action</style> <style id='9'>reward</style>"

    def run_output(self, output, placeholders=None, source=None):
        inp = ILTranslator.TranslateInput(source or self.source, self.placeholders if placeholders is None else placeholders, self.base)
        para = PdfParagraph(pdf_style=self.base)
        self.translator.post_translate_paragraph(para, ParagraphTranslateTracker(), inp, output)
        runs = para.pdf_paragraph_composition
        text = "".join(c.pdf_same_style_unicode_characters.unicode for c in runs if c.pdf_same_style_unicode_characters)
        return para, runs, text

    def test_reported_cached_chinese_tags_restore_original_styles_and_formula(self):
        output = "传统上考虑<样式 id='1'>策略</样式>、<样式 id='3'>状态</样式> {v2}、<样式 id='6'>动作</样式>和<样式 id='9'>奖励</样式>。"
        for _ in range(2):  # The same unmodified bad cache value is repaired each time.
            para, runs, text = self.run_output(output)
            self.assertEqual("传统上考虑策略、状态 、动作和奖励。", text)
            self.assertNotIn("样式", para.unicode)
            styled = [c.pdf_same_style_unicode_characters for c in runs if c.pdf_same_style_unicode_characters and c.pdf_same_style_unicode_characters.pdf_style is self.italic]
            self.assertEqual(["策略", "状态", "动作", "奖励"], [s.unicode for s in styled])
            self.assertTrue(any(c.pdf_formula is self.formula for c in runs))

    def test_valid_output_is_unchanged(self):
        para, _, _ = self.run_output(self.source)
        self.assertEqual(self.source, para.unicode)

    def test_quote_and_traditional_aliases(self):
        for output in (self.source.replace("style", "樣式").replace("'", '"'), self.source.replace("style", "样式").replace("'", "")):
            para, _, _ = self.run_output(output)
            self.assertEqual(self.source, para.unicode)

    def test_missing_duplicate_nested_and_unknown_ids_degrade_without_losing_text(self):
        for output in ("<样式 id='1'>策略", "<样式 id='1'>策略</样式><样式 id='1'>策略</样式>", "<样式 id='1'>策略<样式 id='3'>状态</样式></样式>", "<样式 id='99'>策略</样式>", "<样式 编号='1'>策略</样式>", "<样式 id='1' color='red'>策略</样式>"):
            with self.subTest(output=output):
                para, runs, text = self.run_output(output)
                self.assertIn("策略", text)
                self.assertNotIn("<", text)
                self.assertTrue(all(c.pdf_same_style_unicode_characters.pdf_style is self.base for c in runs if c.pdf_same_style_unicode_characters))

    def test_missing_closing_tag_never_swallows_formula(self):
        _, runs, text = self.run_output("<样式 id='1'>策略 {v2} 后文")
        self.assertIn("后文", text)
        self.assertTrue(any(c.pdf_formula is self.formula for c in runs))

    def test_no_internal_styles_does_not_strip_literal_markup_or_math(self):
        literal = "示例 <样式 id='1'>策略</样式>，x < y，<code>code</code>"
        para, _, text = self.run_output(literal, placeholders=[])
        self.assertEqual(literal, para.unicode)
        self.assertEqual(literal, text)

    def test_literal_unknown_tag_with_internal_styles_is_preserved(self):
        literal = " <样式 id='99'>示例</样式>"
        para, _, text = self.run_output(self.source + literal, source=self.source + literal)
        self.assertIn(literal, para.unicode)
        self.assertIn(literal, text)


if __name__ == "__main__":
    unittest.main()
