"""Post-process Markdown so the output is uniform enough for training data.

Focus:
* Unicode normalization (NFC) and removal of invisible/zero-width glyphs.
* Whitespace normalization (tabs, NBSP, consecutive blank lines).
* Structural tidy-up: spacing around headings/lists/tables, trimmed line
  endings, no leading blank lines, guaranteed single trailing newline.
* De-noising: drop link-only UI artifacts ("Skip to content", "Back to top"),
  de-duplicate consecutive identical paragraphs, collapse repeated headings.

Nothing here alters substantive text — just structure and noise.
"""

from __future__ import annotations

import re
import unicodedata

_UI_NOISE_PATTERNS = [
    re.compile(r"^\s*skip to (main )?content\s*$", re.IGNORECASE),
    re.compile(r"^\s*back to top\s*$", re.IGNORECASE),
    re.compile(r"^\s*(share|tweet|pin it|copy link)\s*$", re.IGNORECASE),
    re.compile(r"^\s*cookie(s)? (preferences|settings|policy)\s*$", re.IGNORECASE),
    re.compile(r"^\s*subscribe( to newsletter)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*accept( all)?( cookies)?\s*$", re.IGNORECASE),
]

_INVISIBLE_RE = re.compile(
    "["
    "​"  # zero-width space
    "‌"  # zero-width non-joiner
    "‍"  # zero-width joiner
    "⁠"  # word joiner
    "﻿"  # BOM
    "]"
)

_NBSP_RE = re.compile("[   ]")
_SPECIAL_SPACE_RE = re.compile("[ - ]")


def _normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _NBSP_RE.sub(" ", text)
    text = _SPECIAL_SPACE_RE.sub(" ", text)
    return text


def _normalize_line(line: str) -> str:
    # Convert tabs to spaces, rtrim.
    line = line.replace("\t", "    ").rstrip()
    # Collapse runs of >1 space *except* inside code fences (handled at block level).
    return line


def _strip_noise(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if any(p.match(line) for p in _UI_NOISE_PATTERNS):
            continue
        out.append(line)
    return out


def _dedupe_consecutive(lines: list[str]) -> list[str]:
    out: list[str] = []
    prev: str | None = None
    for line in lines:
        if prev is not None and line.strip() and line == prev:
            continue
        out.append(line)
        prev = line
    return out


def _tighten_blank_lines(text: str) -> str:
    # Collapse 3+ blank lines → 2 (one empty line).
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Ensure headings have a blank line before them.
    text = re.sub(r"([^\n])\n(#{1,6} )", r"\1\n\n\2", text)
    # Ensure blank line after a heading.
    text = re.sub(r"(^|\n)(#{1,6} [^\n]*)\n(?!\n)", r"\1\2\n\n", text)
    # Blank line before / after fenced code blocks.
    text = re.sub(r"([^\n])\n(```)", r"\1\n\n\2", text)
    text = re.sub(r"(```[^\n]*\n(?:[^\n]*\n)*?```)\n(?!\n)", r"\1\n\n", text)
    # Blank line before a table block — but never between consecutive table rows.
    text = re.sub(r"(\n)([^|\n][^\n]*)\n(\| [^\n]*\|)", r"\1\2\n\n\3", text)
    # Blank line after the last row of a table block.
    text = re.sub(r"(\| [^\n]*\|)\n([^|\n])", r"\1\n\n\2", text)
    return text


def _fix_inline_markdown(text: str) -> str:
    # Repair broken links: "] (" → "]("
    text = re.sub(r"\]\s+\(", "](", text)
    # Remove stray soft-hyphens.
    text = text.replace("­", "")
    # Replace "&nbsp;" literal (sometimes surviving unescaped).
    text = text.replace("&nbsp;", " ")
    return text


def clean_markdown(markdown: str) -> str:
    text = _normalize_unicode(markdown)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Operate per-line for trimming/noise removal, but keep code fences intact.
    lines = text.split("\n")
    in_fence = False
    normalized: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            normalized.append(line.rstrip())
            continue
        if in_fence:
            # Preserve internal whitespace of code blocks.
            normalized.append(line.rstrip())
        else:
            normalized.append(_normalize_line(line))

    normalized = _strip_noise(normalized)
    normalized = _dedupe_consecutive(normalized)

    text = "\n".join(normalized)
    text = _fix_inline_markdown(text)
    text = _tighten_blank_lines(text)

    text = text.strip() + "\n"
    return text
