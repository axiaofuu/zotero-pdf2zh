import copy
import unittest

from babeldoc.format.pdf.document_il import Box
from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il import PdfCharacter
from babeldoc.format.pdf.document_il import PdfFont
from babeldoc.format.pdf.document_il import PdfLine
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfStyle
from babeldoc.format.pdf.document_il import VisualBbox
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.utils.structure_helper import merge_word_fragments
from babeldoc.format.pdf.document_il.utils.structure_helper import preserve_code_blocks
from babeldoc.format.pdf.document_il.utils.structure_helper import split_numbered_lists


def line(text, y=100, x=10, font="text"):
    chars = []
    for i, c in enumerate(text):
        box = Box(x + i * 5, y, x + (i + 1) * 5, y + 10)
        chars.append(
            PdfCharacter(
                char_unicode=c,
                box=box,
                visual_bbox=VisualBbox(box),
                pdf_style=PdfStyle(font_id=font, font_size=10),
                pdf_character_id=ord(c),
                xobj_id=0,
            )
        )
    return PdfParagraphComposition(
        pdf_line=PdfLine(pdf_character=chars, box=Box(x, y, x + len(text) * 5, y + 10))
    )


def paragraph(*lines, label="plain text"):
    return PdfParagraph(
        pdf_paragraph_composition=list(lines),
        layout_label=label,
        layout_id=1,
        xobj_id=0,
        debug_id="source",
    )


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.page = Page(
            pdf_font=[
                PdfFont(font_id="text", name="NewCM10-Regular"),
                PdfFont(font_id="code", name="ABCDEF+DejaVuSansMono"),
            ]
        )
        self.finder = object.__new__(ParagraphFinder)

    def split(self, para):
        paras = [para]
        split_numbered_lists(self.page, paras, self.finder.update_paragraph_data)
        return paras

    def test_numbered_items_keep_continuations_and_hanging_indent(self):
        paras = self.split(
            paragraph(
                line("1. First item", 100),
                line("continued here", 85, x=25),
                line("2. Second item", 70),
            )
        )
        self.assertEqual(2, len(paras))
        self.assertEqual(2, len(paras[0].pdf_paragraph_composition))
        self.assertEqual("First item continued here", paras[0].unicode)
        self.assertEqual(25, paras[0].box.x)
        self.assertEqual(
            "1.2.", "".join(c.char_unicode for c in self.page.pdf_character)
        )
        self.assertEqual([100, 100, 70, 70], [c.box.y for c in self.page.pdf_character])

    def test_alphabetic_and_parenthesized_lists(self):
        for a, b in [("(a)", "(b)"), ("1)", "2)"), ("(1)", "(2)"), ("a.", "b.")]:
            with self.subTest(a=a):
                self.assertEqual(
                    2,
                    len(
                        self.split(paragraph(line(a + " First"), line(b + " Next", 80)))
                    ),
                )

    def test_not_decimals_sections_equations_or_inline_references(self):
        for a, b in [
            ("1.2 Section", "1.3 Other"),
            ("1. 2 + x", "2. 3 + y"),
            ("See (a) above", "then (b) below"),
            ("1. Heading", "2. Heading"),
        ]:
            label = "title" if a == "1. Heading" else "plain text"
            with self.subTest(a=a):
                self.assertEqual(
                    1, len(self.split(paragraph(line(a), line(b, 80), label=label)))
                )
        self.assertEqual(
            1, len(self.split(paragraph(line("1. Alone"), line("ordinary wrap", 80))))
        )
        self.assertEqual(
            1, len(self.split(paragraph(line("1. First"), line("2. Second", 80, x=80))))
        )

    def test_code_glyphs_and_coordinates_are_unchanged(self):
        code = paragraph(
            line("for idx in range(4):", 100, font="code"),
            line("    loss.backward()", 85, font="code"),
            line("    optimizer.step()", 70, font="code"),
        )
        expected = [
            c
            for comp in code.pdf_paragraph_composition
            for c in comp.pdf_line.pdf_character
        ]
        before = copy.deepcopy(expected)
        paras = [code]
        self.assertEqual(1, preserve_code_blocks(self.page, paras))
        self.assertEqual([], paras)
        self.assertEqual(before, self.page.pdf_character)
        self.assertTrue(
            all(a is b for a, b in zip(expected, self.page.pdf_character, strict=True))
        )
        self.assertEqual(0, preserve_code_blocks(self.page, paras))

    def test_contiguous_word_fragment_rejoined_across_layouts(self):
        body = paragraph(line("Use Trainer"[:-1], 100))
        fragment = paragraph(line("r.", 100, x=60))
        fragment.layout_id = 99
        fragment.layout_label = "fallback_line"
        paras = [body, fragment]
        self.assertEqual(
            1,
            merge_word_fragments(
                paras, self.finder.update_paragraph_data, self.finder.update_line_data
            ),
        )
        self.assertEqual("Use Trainer.", paras[0].unicode)
        self.assertEqual(1, len(paras))

    def test_spaced_or_different_baseline_fragment_is_not_merged(self):
        for x, y in [(65, 100), (60, 80)]:
            paras = [paragraph(line("Use Traine", 100)), paragraph(line("r.", y, x=x))]
            self.assertEqual(
                0,
                merge_word_fragments(
                    paras,
                    self.finder.update_paragraph_data,
                    self.finder.update_line_data,
                ),
            )
            self.assertEqual(2, len(paras))

    def test_inline_code_and_monospace_prose_remain_translatable(self):
        prose = paragraph(
            line("We call loss.backward()", 100), line("then train again.", 85)
        )
        mono_prose = paragraph(
            line("A quoted paragraph", 100, font="code"),
            line("with another line", 85, font="code"),
        )
        singleton = paragraph(line("value = 4", font="code"))
        paras = [prose, mono_prose, singleton]
        self.assertEqual(0, preserve_code_blocks(self.page, paras))
        self.assertEqual(3, len(paras))


if __name__ == "__main__":
    unittest.main()
