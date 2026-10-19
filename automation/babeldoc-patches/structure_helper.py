"""Conservative preservation of numbered lists and multiline source code.

Operate on source lines, before formula grouping and translation discard their
boundaries. Inline monospace text and ordinary PDF soft wraps remain translatable.
"""

import copy
import re

from babeldoc.format.pdf.document_il import PdfParagraphComposition

_MONOSPACE = re.compile(r"mono|courier|consolas|menlo|inconsolata|typewriter", re.I)
_MARKER = re.compile(
    r"^(?P<label>\d{1,3}[.)]|\(\d{1,3}\)|\([a-zA-Z]\)|[a-zA-Z][.)])\s+(?P<body>\S)"
)
_CODE = re.compile(
    r"^\s*(?:(?:def|class)\s+\w+|(?:for|while|if|elif|with|except)\s+.+:\s*$"
    r"|(?:else|try):\s*$|(?:import|from)\s+\w+|return(?:\s|$))"
    r"|(?<![=<>!])=(?!=)|\b[A-Za-z_]\w*(?:\.\w+)*\("
    r"|^\s*[\"'][^\"']+[\"']\s*:"
)


def _font_names(page):
    result = {(None, f.font_id): f.name for f in page.pdf_font}
    for obj in page.pdf_xobject:
        for font in obj.pdf_font:
            result[(obj.xobj_id, font.font_id)] = font.name
    return result


def preserve_code_blocks(page, paragraphs):
    """Pass through whole multiline, overwhelmingly monospace code paragraphs.

    The original characters retain glyph IDs, baselines, whitespace, and render
    order. Do not reconstruct strings: underscore glyphs may be separate IL lines.
    Disable this path in OCR mode at the caller, where source glyphs aren't used.
    """
    fonts = _font_names(page)
    kept = []
    count = 0
    for paragraph in paragraphs:
        lines = [c.pdf_line for c in paragraph.pdf_paragraph_composition if c.pdf_line]
        chars = [c for line in lines for c in line.pdf_character]
        visible = [c for c in chars if c.char_unicode and not c.char_unicode.isspace()]
        mono = sum(
            bool(
                _MONOSPACE.search(
                    fonts.get(
                        (c.xobj_id, c.pdf_style.font_id),
                        fonts.get((None, c.pdf_style.font_id), ""),
                    )
                    or ""
                )
            )
            for c in visible
        )
        syntax_lines = sum(
            bool(_CODE.search("".join(c.char_unicode for c in line.pdf_character)))
            for line in lines
        )
        # A table of short identifiers or a prose paragraph with inline code is
        # insufficient evidence. Require code syntax on multiple source lines.
        if (
            len(lines) >= 2
            and len(visible) >= 12
            and mono / max(len(visible), 1) >= 0.95
            and syntax_lines >= 2
            and paragraph.layout_label not in {"table", "title", "abandon"}
        ):
            page.pdf_character.extend(chars)
            count += 1
        else:
            kept.append(paragraph)
    paragraphs[:] = kept
    return count


def _marker(line):
    if not line or not line.pdf_character:
        return None
    chars = line.pdf_character
    text = "".join(c.char_unicode for c in chars)
    match = _MARKER.match(text)
    if not match:
        return None
    label = match.group("label")
    value = label.strip("().")
    kind = ("number" if value.isdigit() else "alpha", label.replace(value, "#"))
    ordinal = int(value) if value.isdigit() else ord(value.lower()) - ord("a") + 1
    # Character indices are not string offsets when ligatures span codepoints.
    end = 0
    body_index = None
    for i, char in enumerate(chars):
        if end == match.start("body"):
            body_index = i
            break
        end += len(char.char_unicode)
    if body_index is None or not chars[body_index].char_unicode[0].isalpha():
        return None
    return kind, ordinal, chars[0].visual_bbox.box.x, body_index


def split_numbered_lists(page, paragraphs, update):
    """Split sequential, aligned list items and keep labels at source positions.

    Requiring a sibling with the next ordinal avoids treating decimals, lone
    numbered headings, equations, and sentence-internal references as lists.
    Continuation lines stay with the preceding item; body boxes exclude labels
    so translated continuation lines retain the source hanging indentation.
    """
    result = []
    for paragraph in paragraphs:
        comps = paragraph.pdf_paragraph_composition
        if paragraph.layout_label in {"title", "table", "abandon"}:
            result.append(paragraph)
            continue
        candidates = [(i, _marker(c.pdf_line)) for i, c in enumerate(comps)]
        candidates = [(i, m) for i, m in candidates if m]
        accepted = set()
        for (i, a), (j, b) in zip(candidates, candidates[1:], strict=False):
            size = comps[i].pdf_line.pdf_character[0].pdf_style.font_size or 10
            if (
                a[0] == b[0]
                and b[1] == a[1] + 1
                and abs(a[2] - b[2]) <= max(2, size * 0.5)
            ):
                accepted.update((i, j))
        if not accepted:
            result.append(paragraph)
            continue
        cuts = sorted({0, *accepted, len(comps)})
        markers = dict(candidates)
        for start, end in zip(cuts, cuts[1:], strict=False):
            item = copy.copy(paragraph)
            item.pdf_paragraph_composition = comps[start:end]
            if start in accepted:
                first_line = copy.copy(item.pdf_paragraph_composition[0].pdf_line)
                chars = first_line.pdf_character
                body_index = markers[start][3]
                page.pdf_character.extend(
                    c for c in chars[:body_index] if c.char_unicode.strip()
                )
                first_line.pdf_character = chars[body_index:]
                item.pdf_paragraph_composition[0] = PdfParagraphComposition(
                    pdf_line=first_line
                )
                # Do not share a debug ID between independently translated items.
            item.debug_id = None
            update(item, update_unicode=True)
            result.append(item)
    paragraphs[:] = result


def merge_word_fragments(paragraphs, update, update_line):
    """Rejoin tiny layout-boundary fragments only when a word is contiguous.

    Different layout IDs can split a final letter from its word. Matching glyph
    baselines, font, XObject and contiguous advance boxes prevents treating a
    neighbouring column, spaced word, or equation label as a continuation.
    """
    removed = set()
    for index, fragment in enumerate(paragraphs):
        lines = [c.pdf_line for c in fragment.pdf_paragraph_composition if c.pdf_line]
        if len(lines) != 1 or fragment.layout_label not in {
            "plain text",
            "text",
            "fallback_line",
        }:
            continue
        chars = lines[0].pdf_character
        text = "".join(c.char_unicode for c in chars)
        if not re.fullmatch(r"[^\W\d_]{1,2}[.,;:!?]?", text):
            continue
        head = chars[0]
        for other_index, paragraph in enumerate(paragraphs):
            if other_index == index or other_index in removed:
                continue
            if paragraph.layout_label not in {"plain text", "text"}:
                continue
            comps = paragraph.pdf_paragraph_composition
            if not comps or not comps[-1].pdf_line:
                continue
            line = comps[-1].pdf_line
            if len(line.pdf_character) <= len(chars) + 3:
                continue
            tail = line.pdf_character[-1]
            size = tail.pdf_style.font_size or 10
            if (
                tail.char_unicode.isalpha()
                and not tail.vertical
                and not head.vertical
                and tail.xobj_id == head.xobj_id
                and tail.pdf_style.font_id == head.pdf_style.font_id
                and abs(size - (head.pdf_style.font_size or 10)) < 0.1
                and abs(tail.box.y - head.box.y) < size * 0.1
                and abs(tail.box.x2 - head.box.x) < max(0.2, size * 0.05)
            ):
                line.pdf_character.extend(chars)
                update_line(line)
                update(paragraph, update_unicode=True)
                removed.add(index)
                break
    paragraphs[:] = [p for i, p in enumerate(paragraphs) if i not in removed]
    return len(removed)
