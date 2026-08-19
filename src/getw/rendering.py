"""Deterministic lowering from the semantic IR."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Document, GroundedText, GroundingSpan, Node

_INLINE_KINDS = {
    "text",
    "link",
    "strong",
    "emphasis",
    "underline",
    "strikethrough",
    "inline_code",
    "line_break",
    "image",
}


def _normalize_inline(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _plain(node: Node, *, include_nested_blocks: bool = True) -> str:
    if node.kind == "text":
        return node.text or ""
    if node.kind == "line_break":
        return "\n"
    if node.kind == "image":
        return str(node.attrs.get("alt") or node.attrs.get("title") or "")
    if node.kind == "code_block":
        return node.text or "".join(_plain(child) for child in node.children)

    parts: list[str] = []
    for child in node.children:
        if not include_nested_blocks and child.kind not in _INLINE_KINDS:
            continue
        parts.append(_plain(child, include_nested_blocks=include_nested_blocks))
    if node.text:
        parts.insert(0, node.text)
    return _normalize_inline("".join(parts))


def _escape_link_target(value: str) -> str:
    return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _inline_markdown(node: Node, *, include_urls: bool) -> str:
    if node.kind == "text":
        return node.text or ""
    if node.kind == "line_break":
        return "  \n"
    if node.kind == "image":
        alt = str(node.attrs.get("alt") or node.attrs.get("title") or "image")
        source = str(node.attrs.get("url") or "")
        if include_urls and source:
            return f"![{alt}]({_escape_link_target(source)})"
        return f"[Image: {alt}]"

    content = "".join(
        _inline_markdown(child, include_urls=include_urls) for child in node.children
    )
    if node.text:
        content = node.text + content

    if node.kind == "link":
        target = str(node.attrs.get("url") or "")
        if include_urls and target and content.strip():
            return f"[{content.strip()}]({_escape_link_target(target)})"
        return content
    if node.kind == "strong":
        return f"**{content}**"
    if node.kind == "emphasis":
        return f"*{content}*"
    if node.kind == "underline":
        return content
    if node.kind == "strikethrough":
        return f"~~{content}~~"
    if node.kind == "inline_code":
        ticks = "``" if "`" in content else "`"
        return f"{ticks}{content}{ticks}"
    return content


def _direct_inline_markdown(node: Node, *, include_urls: bool) -> str:
    parts = [
        _inline_markdown(child, include_urls=include_urls)
        for child in node.children
        if child.kind in _INLINE_KINDS
    ]
    if node.text:
        parts.insert(0, node.text)
    return _normalize_inline("".join(parts))


def _table_rows(node: Node) -> list[tuple[list[str], bool]]:
    rows: list[tuple[list[str], bool]] = []
    for row in node.children:
        if row.kind != "table_row":
            continue
        cells: list[str] = []
        is_header = False
        for cell in row.children:
            if cell.kind != "table_cell":
                continue
            value = _plain(cell).replace("|", "\\|").replace("\n", " ").strip()
            cells.append(value)
            is_header = is_header or bool(cell.attrs.get("header"))
        if cells:
            rows.append((cells, is_header))
    return rows


def _render_table(node: Node) -> str:
    rows = _table_rows(node)
    if not rows:
        return ""
    width = max(len(cells) for cells, _ in rows)
    normalized = [
        (cells + [""] * (width - len(cells)), header) for cells, header in rows
    ]
    header_count = 0
    for _, header in normalized:
        if not header:
            break
        header_count += 1

    if header_count:
        header_rows = [row for row, _ in normalized[:header_count]]
        header = [
            " / ".join(dict.fromkeys(row[index] for row in header_rows if row[index]))
            for index in range(width)
        ]
        lines = [
            "| " + " | ".join(cell or " " for cell in header) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
        ]
        lines.extend(
            "| " + " | ".join(cell or " " for cell in row) + " |"
            for row, _ in normalized[header_count:]
        )
        return "\n".join(lines)

    # GFM requires a header row. A fenced TSV preserves the fact that the
    # source had no header instead of silently promoting its first data row.
    body = "\n".join("\t".join(row) for row, _ in normalized)
    return f"```tsv\n{body}\n```"


def _code_fence(value: str) -> str:
    longest = max((len(item) for item in re.findall(r"`+", value)), default=0)
    return "`" * max(3, longest + 1)


def _render_list(node: Node, *, include_urls: bool, depth: int = 0) -> str:
    ordered = bool(node.attrs.get("ordered"))
    lines: list[str] = []
    item_number = 1
    for item in node.children:
        if item.kind != "list_item":
            continue
        marker = f"{item_number}." if ordered else "-"
        direct = _direct_inline_markdown(item, include_urls=include_urls)
        indent = "  " * depth
        lines.append(f"{indent}{marker} {direct}".rstrip())
        for child in item.children:
            if child.kind == "list":
                nested = _render_list(child, include_urls=include_urls, depth=depth + 1)
                if nested:
                    lines.append(nested)
        item_number += 1
    return "\n".join(lines)


def _render_block(node: Node, *, include_urls: bool) -> str:
    if node.kind == "heading":
        raw_level = node.attrs.get("level")
        level = int(raw_level) if isinstance(raw_level, (str, int, float)) else 2
        level = max(1, min(6, level))
        return f"{'#' * level} {_direct_inline_markdown(node, include_urls=include_urls)}".rstrip()
    if node.kind == "paragraph":
        return _direct_inline_markdown(node, include_urls=include_urls)
    if node.kind == "list":
        return _render_list(node, include_urls=include_urls)
    if node.kind == "quote":
        value = _render_children(node.children, include_urls=include_urls)
        if not value:
            value = _direct_inline_markdown(node, include_urls=include_urls)
        return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())
    if node.kind == "code_block":
        value = node.text if node.text is not None else _plain(node)
        language = str(node.attrs.get("language") or "")
        fence = _code_fence(value)
        return f"{fence}{language}\n{value.rstrip()}\n{fence}"
    if node.kind == "table":
        return _render_table(node)
    if node.kind == "image":
        return _inline_markdown(node, include_urls=include_urls)
    if node.kind in _INLINE_KINDS:
        return _inline_markdown(node, include_urls=include_urls)
    return _render_children(node.children, include_urls=include_urls)


def _render_children(children: Iterable[Node], *, include_urls: bool) -> str:
    rendered = [
        value
        for child in children
        if (value := _render_block(child, include_urls=include_urls)).strip()
    ]
    return "\n\n".join(rendered)


def render_document(
    document: Document,
    format: str,
) -> str:
    if format not in {"compact", "markdown", "plain"}:
        raise ValueError(f"Unsupported render format: {format}")

    if format == "plain":
        return grounding_text(document.root).text.rstrip() + "\n"

    body = _render_children(
        document.root.children,
        include_urls=format == "markdown",
    ).strip()
    if format == "markdown":
        return body + "\n"

    metadata: list[str] = []
    source_url = document.source.final_url or document.source.requested_url
    if source_url:
        metadata.append(f"Source: {source_url}")

    first_heading = next(
        (child for child in document.root.children if child.kind == "heading"), None
    )
    first_heading_text = _plain(first_heading) if first_heading else ""
    if document.metadata.title and document.metadata.title != first_heading_text:
        metadata.append(f"Title: {document.metadata.title}")
    if document.metadata.authors:
        metadata.append(f"Author: {', '.join(document.metadata.authors)}")
    if document.metadata.published_at:
        metadata.append(f"Published: {document.metadata.published_at}")
    if document.metadata.language:
        metadata.append(f"Language: {document.metadata.language}")

    parts = [part for part in ("\n".join(metadata), body) if part]
    return "\n\n".join(parts).rstrip() + "\n"


def _grounding_units(node: Node) -> Iterable[tuple[str, str]]:
    if node.kind in {"heading", "paragraph"}:
        value = _plain(node)
        if value:
            yield node.id, value
        return
    if node.kind == "list_item":
        value = _plain(node, include_nested_blocks=False)
        if value:
            yield node.id, value
        for child in node.children:
            if child.kind == "list":
                yield from _grounding_units(child)
        return
    if node.kind == "list":
        for child in node.children:
            yield from _grounding_units(child)
        return
    if node.kind in {"quote", "code_block"}:
        value = _plain(node)
        if value:
            yield node.id, value
        return
    if node.kind == "table":
        for row in node.children:
            if row.kind != "table_row":
                continue
            cells = [
                _plain(cell).strip()
                for cell in row.children
                if cell.kind == "table_cell"
            ]
            value = "\t".join(cells).strip()
            if value:
                yield row.id, value
        return
    if node.kind == "image":
        value = _plain(node)
        if value:
            yield node.id, value
        return
    for child in node.children:
        yield from _grounding_units(child)


def grounding_text(root: Node) -> GroundedText:
    chunks: list[str] = []
    spans: list[GroundingSpan] = []
    cursor = 0
    for node_id, value in _grounding_units(root):
        value = value.strip()
        if not value:
            continue
        if chunks:
            chunks.append("\n\n")
            cursor += 2
        start = cursor
        chunks.append(value)
        cursor += len(value)
        spans.append(GroundingSpan(node_id=node_id, start=start, end=cursor))
    return GroundedText(text="".join(chunks), spans=tuple(spans))
