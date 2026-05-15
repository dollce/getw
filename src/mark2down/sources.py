"""Input source routing and lightweight document conversion."""

from __future__ import annotations

import csv
import base64
import io
import json
import mimetypes
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from charset_normalizer import from_bytes
from bs4 import BeautifulSoup

from .agent import FetchResult, fetch
from .cleaner import clean_markdown
from .converter import html_to_markdown
from .documents import convert_document_bytes, is_supported_document
from .ocr import OcrOptions


@dataclass
class ContentResult:
    """Converted Markdown plus source metadata used by the CLI."""

    source: str
    source_type: str
    markdown: str
    title: str = ""
    final_url: str | None = None
    lang: str = ""
    canonical: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    json_ld: list[Any] = field(default_factory=list)
    status: int | None = None
    path: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    charset: str | None = None


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"}


def is_file_uri(source: str) -> bool:
    return urlparse(source).scheme == "file"


def is_data_uri(source: str) -> bool:
    return source.startswith("data:")


def _normalize_extension(extension: str | None) -> str | None:
    if not extension:
        return None
    extension = extension.strip().lower()
    if not extension:
        return None
    return extension if extension.startswith(".") else f".{extension}"


def _decode_text(data: bytes, charset: str | None) -> tuple[str, str | None]:
    if charset:
        return data.decode(charset), charset
    detected = from_bytes(data).best()
    if detected is None:
        return data.decode("utf-8", errors="replace"), "utf-8"
    encoding = detected.encoding or "utf-8"
    return str(detected), encoding


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    return b"\x00" in sample


def _infer_document_type(
    data: bytes,
    extension: str | None,
    mime_type: str | None,
) -> tuple[str | None, str | None]:
    if extension or mime_type:
        return extension, mime_type
    if data.startswith(b"%PDF"):
        return ".pdf", "application/pdf"
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return extension, mime_type
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return extension, mime_type
    if "word/document.xml" in names:
        return ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "ppt/presentation.xml" in names:
        return ".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if "xl/workbook.xml" in names:
        return ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return extension, mime_type


def _looks_like_html(text: str) -> bool:
    stripped = text.lstrip().lower()
    return stripped.startswith(("<!doctype html", "<html", "<body", "<article")) or (
        "<html" in stripped[:500] and "</html>" in stripped
    )


def _looks_like_jsonl(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        for line in lines:
            json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def _sniff_csv_dialect(text: str) -> csv.Dialect | None:
    sample = text[:8192]
    if not sample.strip():
        return None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        return None
    rows = list(csv.reader(io.StringIO(sample), dialect))
    meaningful = [row for row in rows if any(cell.strip() for cell in row)]
    if len(meaningful) < 2:
        return None
    widths = [len(row) for row in meaningful]
    if max(widths) < 2:
        return None
    return dialect


def _infer_text_extension(
    text: str,
    extension: str | None,
    mime_type: str | None,
) -> tuple[str | None, csv.Dialect | None]:
    if extension:
        normalized_extension = extension if extension.startswith(".") else f".{extension}"
        if normalized_extension.lower() == ".tsv":
            return normalized_extension, csv.excel_tab
        return normalized_extension, None
    mime_lc = (mime_type or "").lower()
    if mime_lc.startswith(("text/html", "application/xhtml")) or _looks_like_html(text):
        return ".html", None
    if mime_lc in {"application/json"}:
        return ".json", None
    if mime_lc in {"application/x-ndjson", "application/jsonl"}:
        return ".jsonl", None
    if mime_lc in {"text/csv", "application/csv"}:
        return ".csv", None
    if mime_lc == "text/tab-separated-values":
        return ".tsv", None
    if _looks_like_jsonl(text):
        return ".jsonl", None
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return ".json", None
    dialect = _sniff_csv_dialect(text)
    if dialect is not None:
        return ".tsv" if dialect.delimiter == "\t" else ".csv", dialect
    return extension, None


def _escape_table_cell(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


def _csv_to_markdown(text: str, dialect: csv.Dialect | None = None) -> str:
    rows = list(csv.reader(io.StringIO(text), dialect or csv.excel))
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = [_escape_table_cell(cell) for cell in normalized[0]]
    body = [[_escape_table_cell(cell) for cell in row] for row in normalized[1:]]

    lines = [
        "| " + " | ".join(cell or " " for cell in header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(cell or " " for cell in row) + " |")
    return "\n".join(lines)


def _json_to_markdown(text: str, extension: str | None) -> str:
    if extension == ".jsonl":
        lines: list[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            lines.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        body = "\n".join(lines)
        return f"```jsonl\n{body}\n```"

    value = json.loads(text)
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return f"```json\n{body}\n```"


def convert_bytes(
    data: bytes,
    *,
    source_name: str,
    source_type: str,
    base_url: str,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
    path: str | None = None,
    ocr: OcrOptions | None = None,
) -> ContentResult:
    """Convert bytes from a local file or stdin into LLM-ready Markdown."""
    extension = _normalize_extension(extension)
    if mime_type is None and extension:
        mime_type = mimetypes.guess_type(f"placeholder{extension}", strict=False)[0]
    extension, mime_type = _infer_document_type(data, extension, mime_type)

    if is_supported_document(extension=extension, mime_type=mime_type):
        converted = convert_document_bytes(
            data,
            source_name=source_name,
            base_url=base_url,
            extension=extension,
            mime_type=mime_type,
            ocr=ocr,
        )
        title = converted.title or (Path(source_name).name if source_name != "-" else "stdin")
        return ContentResult(
            source=source_name,
            source_type=source_type,
            markdown=clean_markdown(converted.markdown),
            title=title,
            path=path,
            mime_type=mime_type,
            extension=extension,
            charset=None,
        )

    if _looks_binary(data):
        raise ValueError(
            "Unsupported binary input. Provide PDF, DOCX, PPTX, XLSX, HTML, Markdown, text, CSV, JSON, or JSONL."
        )

    text, resolved_charset = _decode_text(data, charset)
    mime_lc = (mime_type or "").lower()
    extension, csv_dialect = _infer_text_extension(text, extension, mime_type)

    title = Path(source_name).name if source_name != "-" else "stdin"

    if extension in {".html", ".htm"} or mime_lc.startswith(("text/html", "application/xhtml")):
        soup = BeautifulSoup(text, "lxml")
        if soup.title and soup.title.string:
            title = soup.title.string.strip() or title
        markdown = html_to_markdown(text, base_url)
    elif extension in {".csv", ".tsv"} or mime_lc in {"text/csv", "application/csv", "text/tab-separated-values"}:
        markdown = _csv_to_markdown(text, csv_dialect)
    elif extension in {".json", ".jsonl"} or mime_lc in {"application/json", "application/x-ndjson"}:
        markdown = _json_to_markdown(text, extension)
    else:
        markdown = text

    markdown = clean_markdown(markdown)
    return ContentResult(
        source=source_name,
        source_type=source_type,
        markdown=markdown,
        title=title,
        path=path,
        mime_type=mime_type,
        extension=extension,
        charset=resolved_charset,
    )


def convert_url(
    url: str,
    *,
    wait_seconds: float = 1.0,
    wait_selector: str | None = None,
    timeout_ms: int = 45_000,
    user_agent: str | None = None,
    headless: bool = True,
    extra_http_headers: dict[str, str] | None = None,
    ocr: OcrOptions | None = None,
) -> ContentResult:
    fetched: FetchResult = fetch(
        url,
        wait_seconds=wait_seconds,
        wait_selector=wait_selector,
        timeout_ms=timeout_ms,
        user_agent=user_agent or None,  # type: ignore[arg-type]
        headless=headless,
        extra_http_headers=extra_http_headers,
        ocr=ocr,
    )
    markdown = clean_markdown(html_to_markdown(fetched.html, fetched.final_url))
    return ContentResult(
        source=url,
        source_type="url",
        markdown=markdown,
        title=fetched.title,
        final_url=fetched.final_url,
        lang=fetched.lang,
        canonical=fetched.canonical,
        meta=fetched.meta,
        json_ld=fetched.json_ld,
        status=fetched.status,
        mime_type="text/html",
        extension=".html",
    )


def convert_local_file(
    path: Path,
    *,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
    ocr: OcrOptions | None = None,
) -> ContentResult:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(str(resolved))
    if not resolved.is_file():
        raise ValueError(f"Input path is not a file: {resolved}")
    ext = _normalize_extension(extension) or resolved.suffix.lower() or None
    return convert_bytes(
        resolved.read_bytes(),
        source_name=str(resolved),
        source_type="file",
        base_url=resolved.as_uri(),
        mime_type=mime_type,
        extension=ext,
        charset=charset,
        path=str(resolved),
        ocr=ocr,
    )


def convert_file_uri(
    uri: str,
    *,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
    ocr: OcrOptions | None = None,
) -> ContentResult:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported URI scheme: {parsed.scheme}")
    if parsed.netloc and parsed.netloc != "localhost":
        raise ValueError(f"Unsupported file URI host: {parsed.netloc}")
    return convert_local_file(
        Path(unquote(parsed.path)),
        mime_type=mime_type,
        extension=extension,
        charset=charset,
        ocr=ocr,
    )


def convert_data_uri(
    uri: str,
    *,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
    ocr: OcrOptions | None = None,
) -> ContentResult:
    if not uri.startswith("data:") or "," not in uri:
        raise ValueError("Invalid data URI.")
    header, payload = uri[5:].split(",", 1)
    parts = [part for part in header.split(";") if part]
    inferred_mime = parts[0] if parts and "/" in parts[0] else None
    is_base64 = any(part.lower() == "base64" for part in parts[1:] if part)
    inferred_charset = charset
    for part in parts[1:]:
        if part.lower().startswith("charset=") and inferred_charset is None:
            inferred_charset = part.split("=", 1)[1]

    data = base64.b64decode(payload) if is_base64 else unquote(payload).encode("utf-8")
    return convert_bytes(
        data,
        source_name="data-uri",
        source_type="data",
        base_url="",
        mime_type=mime_type or inferred_mime,
        extension=extension,
        charset=inferred_charset,
        ocr=ocr,
    )
