"""Custom HTML → Markdown table conversion.

markdownify's built-in table handler trips over `|` in cells, multi-line
content, rowspan/colspan, and nested tables.  This module walks each
`<table>` to build a rectangular grid, expanding spans into duplicated
values, and then emits a GitHub-flavored Markdown table that never breaks.
"""

from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

_WS_RE = re.compile(r"\s+")
_NEWLINE_RE = re.compile(r"\s*\n\s*")


def _inline_text(cell: Tag) -> str:
    """Extract cell text while preserving inline markup that survives in a table cell."""
    # Replace <br> with a newline so we can convert to <br> in markdown later.
    for br in list(cell.find_all("br")):
        br.replace_with(NavigableString("\n"))

    # Inline code: wrap in backticks.
    for code in list(cell.find_all("code")):
        code.replace_with(NavigableString(f"`{code.get_text()}`"))

    # Strong/bold.
    for tag in list(cell.find_all(["strong", "b"])):
        inner = tag.get_text()
        if inner.strip():
            tag.replace_with(NavigableString(f"**{inner}**"))
        else:
            tag.decompose()

    # Emphasis/italic.
    for tag in list(cell.find_all(["em", "i"])):
        inner = tag.get_text()
        if inner.strip():
            tag.replace_with(NavigableString(f"*{inner}*"))
        else:
            tag.decompose()

    # Links: keep label + url.
    for a in list(cell.find_all("a")):
        href = a.get("href", "").strip()
        label = a.get_text(separator=" ", strip=True)
        if href and label:
            a.replace_with(NavigableString(f"[{label}]({href})"))
        elif label:
            a.replace_with(NavigableString(label))
        else:
            a.decompose()

    text = cell.get_text(separator=" ")
    # Preserve newlines as <br> so pipes remain on one logical line.
    text = _NEWLINE_RE.sub(" <br> ", text)
    # Escape pipes after the newline-to-<br> substitution.
    text = text.replace("|", "\\|")
    # Collapse whitespace runs (but <br> is already a token).
    text = _WS_RE.sub(" ", text).strip()
    # Collapse repeated <br> and strip leading/trailing ones.
    text = re.sub(r"(?:<br>\s*){2,}", "<br> ", text)
    text = re.sub(r"^(?:\s*<br>\s*)+", "", text)
    text = re.sub(r"(?:\s*<br>\s*)+$", "", text)
    return text.strip()


def _iter_cells(tr: Tag) -> Iterable[Tag]:
    for child in tr.find_all(["td", "th"], recursive=False):
        yield child


def _extract_grid(table: Tag) -> tuple[list[list[str]], list[bool]]:
    """Return (grid, header_flags) where header_flags[i] is True for header rows."""
    rows: list[list[str]] = []
    header_flags: list[bool] = []
    pending: dict[tuple[int, int], str] = {}

    tr_list = table.find_all("tr")
    for r_idx, tr in enumerate(tr_list):
        grid_row: list[str] = []
        c_idx = 0
        cells = list(_iter_cells(tr))
        if not cells:
            continue
        cell_iter = iter(cells)
        row_is_header = all(cell.name == "th" for cell in cells)
        # Mark as header if inside <thead>
        in_thead = bool(tr.find_parent("thead"))
        header_flags.append(row_is_header or in_thead)

        while True:
            while (r_idx, c_idx) in pending:
                grid_row.append(pending.pop((r_idx, c_idx)))
                c_idx += 1
            try:
                cell = next(cell_iter)
            except StopIteration:
                break
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
            except ValueError:
                colspan = 1
            try:
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                rowspan = 1
            text = _inline_text(cell)
            for i in range(colspan):
                grid_row.append(text)
                for j in range(1, rowspan):
                    pending[(r_idx + j, c_idx + i)] = text
                c_idx += 1
        rows.append(grid_row)

    if not rows:
        return [], []

    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    return rows, header_flags


def _is_effectively_empty(row: list[str]) -> bool:
    return all(not cell.strip() for cell in row)


def _is_layout_table(table: Tag) -> bool:
    role = (table.get("role") or "").lower()
    if role in {"presentation", "none"}:
        return True
    classes = " ".join(table.get("class") or [])
    if re.search(r"\b(ambox|navbox|vertical-navbox|sidebar|metadata|mbox|tmbox|fmbox|ombox|cmbox|imbox)\b", classes):
        return True
    return False


def table_to_markdown(table: Tag) -> str:
    """Render a `<table>` as a Markdown table; empty/layout tables return ''."""
    if _is_layout_table(table):
        return ""

    # Strip nested tables first — they can't be expressed in Markdown; flatten their text.
    for inner in list(table.find_all("table"))[::-1]:
        if _is_layout_table(inner):
            inner.decompose()
            continue
        inner_md = table_to_markdown(inner)
        if inner_md:
            replacement = NavigableString("\n" + inner_md + "\n")
        else:
            replacement = NavigableString(inner.get_text(separator=" ", strip=True))
        inner.replace_with(replacement)

    grid, header_flags = _extract_grid(table)
    if not grid:
        return ""

    # Drop fully empty rows.
    pruned: list[tuple[list[str], bool]] = [
        (row, is_header) for row, is_header in zip(grid, header_flags) if not _is_effectively_empty(row)
    ]
    if not pruned:
        return ""

    # Promote: header rows come first; if none flagged, treat first row as header.
    header_rows = [row for row, flag in pruned if flag]
    body_rows = [row for row, flag in pruned if not flag]
    if not header_rows:
        header_rows = [pruned[0][0]]
        body_rows = [row for row, _ in pruned[1:]]

    # Collapse multi-row headers into a single row (concatenate with " / ").
    if len(header_rows) > 1:
        merged: list[str] = []
        cols = len(header_rows[0])
        for c in range(cols):
            parts = [hr[c] for hr in header_rows if c < len(hr) and hr[c]]
            # De-duplicate consecutive identical pieces (common with rowspan headers).
            deduped: list[str] = []
            for part in parts:
                if not deduped or deduped[-1] != part:
                    deduped.append(part)
            merged.append(" / ".join(deduped))
        header = merged
    else:
        header = header_rows[0]

    cols = len(header)
    body_rows = [row[:cols] + [""] * max(0, cols - len(row)) for row in body_rows]

    lines: list[str] = []
    lines.append("| " + " | ".join(cell or " " for cell in header) + " |")
    lines.append("| " + " | ".join(["---"] * cols) + " |")
    for row in body_rows:
        lines.append("| " + " | ".join(cell or " " for cell in row) + " |")
    return "\n".join(lines)


def replace_tables_with_placeholders(soup: BeautifulSoup) -> list[str]:
    """Swap every top-level `<table>` for a placeholder; nested tables are
    flattened inside ``table_to_markdown`` so we skip them here."""
    tables_md: list[str] = []
    top_level = [t for t in soup.find_all("table") if not t.find_parent("table")]
    for idx, table in enumerate(top_level):
        md = table_to_markdown(table)
        tables_md.append(md)
        placeholder = soup.new_tag("p")
        placeholder.string = f"@@MARK2DOWN_TABLE_{idx}@@"
        table.replace_with(placeholder)
    return tables_md


def inject_tables(markdown: str, tables_md: list[str]) -> str:
    """Substitute table placeholders back into the rendered Markdown."""
    for idx, md in enumerate(tables_md):
        placeholder = f"@@MARK2DOWN_TABLE_{idx}@@"
        replacement = "\n\n" + md + "\n\n" if md else ""
        markdown = markdown.replace(placeholder, replacement)
    return markdown
