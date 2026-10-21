"""Repair translated internal style tags before BabelDOC parses cached or live output.

Only the engine's style placeholders are interpreted. Formula tokens and literal
tags from the input are left alone. Ambiguous style trees degrade to base styling,
never to guessed associations between a translation and a source font.
"""

from collections import Counter
import logging
import re

logger = logging.getLogger(__name__)
_TAG = re.compile(
    r"<\s*(?P<close>/)?\s*(?:style|样式|樣式)\b(?P<attrs>[^<>]*)>",
    re.IGNORECASE,
)
_ID = re.compile(r"\s+id\s*=\s*(?:['\"‘’“”]\s*(?P<quoted>[0-9]+)\s*['\"‘’“”]|(?P<bare>[0-9]+))\s*", re.I)


def _tag_id(match):
    attrs = _ID.fullmatch(match["attrs"])
    return (attrs["quoted"] or attrs["bare"]) if attrs else None


def repair_style_placeholders(output: str, translate_input) -> str:
    expected = {}
    for placeholder in translate_input.placeholders:
        left = getattr(placeholder, "left_placeholder", "")
        right = getattr(placeholder, "right_placeholder", "")
        match = _TAG.fullmatch(left)
        if match and _tag_id(match) and right.lower() == "</style>":
            expected[_tag_id(match)] = (left, right)
    if not expected:
        return output

    source = translate_input.unicode
    stack = []
    edits = []
    seen = Counter()
    valid = True
    for match in _TAG.finditer(output):
        token = match[0]
        if not match["close"]:
            ident = _tag_id(match)
            # Literal markup is content, including localized examples in source.
            literal = ident not in expected and token in source
            literal |= bool(re.search(r"样式|樣式", token)) and token in source
            if literal:
                stack.append((None, True))
                continue
            if stack:
                valid = False  # BabelDOC's style parser requires flat runs.
            stack.append((ident, False))
            seen[ident] += 1
            if ident not in expected:
                valid = False
            edits.append((match.start(), match.end(), expected.get(ident, ("", ""))[0]))
        elif stack:
            if match["attrs"].strip():
                valid = False
            ident, literal = stack.pop()
            if literal:
                continue
            edits.append((match.start(), match.end(), expected.get(ident, ("", ""))[1]))
        elif token not in getattr(translate_input, "original_placeholder_tokens", {}):
            valid = False
            edits.append((match.start(), match.end(), ""))
    if any(not literal for _, literal in stack):
        valid = False
    if seen != Counter({ident: 1 for ident in expected}):
        valid = False

    parts = []
    end = 0
    for start, stop, replacement in edits:
        parts.extend((output[end:start], replacement if valid else ""))
        end = stop
    parts.append(output[end:])
    repaired = "".join(parts)
    if repaired != output:
        if valid:
            logger.info("Repaired translated rich-text style tags before layout (cache-safe).")
        else:
            logger.warning("Invalid rich-text style tags; preserved translated text with base styling.")
    return repaired
