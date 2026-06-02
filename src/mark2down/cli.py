"""mark2down command-line entry point."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
from rich.console import Console
from slugify import slugify

from . import __version__
from .agent import ChallengePageError
from .metadata import (
    build_frontmatter,
    build_html_document,
    build_page_metadata,
    build_source_frontmatter,
)
from .ocr import OcrOptions
from .sources import (
    convert_bytes,
    convert_data_uri,
    convert_file_uri,
    convert_local_file,
    convert_url,
    is_data_uri,
    is_file_uri,
    is_url,
)

console = Console(stderr=True)
DEFAULT_WAIT_SECONDS = 3.0
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_OCR_MAX_IMAGES = 20
_FORMAT_EXTENSIONS = {
    "markdown": ".md",
    "html": ".html",
}
_SUFFIX_FORMATS = {
    ".md": "markdown",
    ".html": "html",
}


def _default_filename(title: str, url: str, extension: str) -> str:
    base = ""
    if title:
        base = slugify(title, max_length=80, word_boundary=True, allow_unicode=True)
    if not base:
        parsed = urlparse(url)
        host = parsed.netloc.replace(".", "-")
        path = parsed.path.strip("/").replace("/", "-") or "index"
        base = slugify(f"{host}-{path}", max_length=80, allow_unicode=True)
    if not base:
        base = "page-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return base + extension


def _resolve_output_path(
    output: Path | None,
    title: str,
    url: str,
    extension: str,
) -> Path:
    if output is None:
        target_dir = Path.cwd().resolve()
        name = _default_filename(title, url, extension)
    else:
        target = output.expanduser()
        if target.exists() and target.is_file():
            target_dir = target.parent.resolve()
            name = target.name
        elif target.suffix.lower() in _SUFFIX_FORMATS:
            target_dir = target.parent.resolve()
            name = target.name
        else:
            target_dir = target.resolve()
            name = _default_filename(title, url, extension)

    target_dir.mkdir(parents=True, exist_ok=True)
    if Path(name).suffix.lower() != extension:
        name += extension
    path = target_dir / name
    # Avoid silent overwrite: append -N suffix when needed.
    counter = 2
    stem = path.stem
    suffix = path.suffix
    while path.exists():
        path = target_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    return path


def _suffix_format(output: Path | None) -> str | None:
    if output is None:
        return None
    return _SUFFIX_FORMATS.get(output.expanduser().suffix.lower())


def _is_html_result(result: object) -> bool:
    extension = getattr(result, "extension", None)
    source_type = getattr(result, "source_type", "")
    html = getattr(result, "html", "")
    return bool(str(html).strip()) and (
        source_type == "url" or extension in {".html", ".htm"}
    )


def _resolve_output_format(
    *,
    output_format: str,
    output: Path | None,
    supports_html: bool,
) -> str:
    suffix_format = _suffix_format(output)
    if output_format != "auto" and suffix_format and suffix_format != output_format:
        raise click.ClickException(
            f"Output suffix implies {suffix_format}, but --format is {output_format}."
        )
    if suffix_format:
        resolved = suffix_format
    elif output_format != "auto":
        resolved = output_format
    else:
        resolved = "html" if supports_html else "markdown"
    if resolved == "html" and not supports_html:
        raise click.ClickException(
            "HTML output is only supported for URL and HTML inputs. "
            "Use --format markdown for this source."
        )
    return resolved


def _markdown_document(result: object, source: str) -> str:
    markdown_body = getattr(result, "markdown")
    if getattr(result, "source_type") == "url":
        final_url = getattr(result, "final_url") or source
        frontmatter = build_frontmatter(
            url=source,
            final_url=final_url,
            title=getattr(result, "title"),
            html_lang=getattr(result, "lang"),
            canonical=getattr(result, "canonical"),
            meta=getattr(result, "meta"),
            json_ld=getattr(result, "json_ld"),
            markdown=markdown_body,
        )
    else:
        frontmatter = build_source_frontmatter(
            source=getattr(result, "source"),
            source_type=getattr(result, "source_type"),
            title=getattr(result, "title"),
            markdown=markdown_body,
            path=getattr(result, "path"),
            mime_type=getattr(result, "mime_type"),
            extension=getattr(result, "extension"),
            charset=getattr(result, "charset"),
        )
    return frontmatter + markdown_body


def _html_document(result: object, source: str) -> str:
    final_url = getattr(result, "final_url") or getattr(result, "source")
    metadata = build_page_metadata(
        url=source if getattr(result, "source_type") == "url" else getattr(result, "source"),
        final_url=final_url,
        title=getattr(result, "title"),
        html_lang=getattr(result, "lang"),
        canonical=getattr(result, "canonical"),
        meta=getattr(result, "meta"),
        json_ld=getattr(result, "json_ld"),
        markdown=getattr(result, "markdown"),
    )
    return build_html_document(
        metadata=metadata,
        content_html=getattr(result, "html"),
        raw_meta=getattr(result, "meta"),
        raw_json_ld=getattr(result, "json_ld"),
    )


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "\b\nExamples:\n"
        "  m2d https://example.com/post\n"
        "  m2d ./report.pdf -o ./notes\n"
        "  m2d https://example.com/post --format markdown\n"
        "  cat data.csv | m2d -o ./notes/data.md"
    ),
)
@click.version_option(__version__, "-V", "--version", prog_name="mark2down")
@click.argument("source", required=False)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(path_type=Path),
    default=None,
    help="Save path. Use a directory, .html file path, or .md file path. Defaults to the current directory.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["auto", "html", "markdown"]),
    default="auto",
    show_default=True,
    help="Output format. Auto writes HTML for URL/HTML inputs and Markdown for other inputs.",
)
def main(
    source: str | None,
    output: Path | None,
    output_format: str,
) -> None:
    """Convert a URL, local file, or stdin to LLM-ready HTML or Markdown."""
    if not source:
        if not sys.stdin.isatty():
            source = "-"
        else:
            raise click.UsageError("Missing argument 'SOURCE'.")

    log = console.log
    ocr_options = OcrOptions(
        enabled=True,
        max_images=DEFAULT_OCR_MAX_IMAGES,
    )

    log(f"[bold cyan]Reading[/] {source}")
    try:
        if is_url(source):
            result = convert_url(
                source,
                wait_seconds=DEFAULT_WAIT_SECONDS,
                timeout_ms=DEFAULT_TIMEOUT_SECONDS * 1000,
                ocr=ocr_options,
            )
        elif is_file_uri(source):
            result = convert_file_uri(source, ocr=ocr_options)
        elif is_data_uri(source):
            result = convert_data_uri(source, ocr=ocr_options)
        elif source == "-":
            result = convert_bytes(
                sys.stdin.buffer.read(),
                source_name="-",
                source_type="stdin",
                base_url="",
                ocr=ocr_options,
            )
        else:
            result = convert_local_file(Path(source), ocr=ocr_options)
    except ChallengePageError as exc:
        # Silent failure is worse than loud failure here: without this the
        # challenge interstitial gets serialized as if it were the real page.
        raise click.ClickException(
            f"Bot challenge page detected ({exc.vendor}): title={exc.title!r} "
            f"status={exc.status}. mark2down cannot solve interactive challenges "
            f"in headless mode."
        ) from exc
    except (FileNotFoundError, UnicodeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    log(
        f"  type={result.source_type} status={result.status or '?'} "
        f"mime={result.mime_type or '?'} title={result.title!r}"
    )

    supports_html = _is_html_result(result)
    resolved_format = _resolve_output_format(
        output_format=output_format,
        output=output,
        supports_html=supports_html,
    )
    log(f"[bold cyan]Writing[/] {resolved_format}")

    markdown_body = result.markdown

    if not markdown_body.strip():
        raise click.ClickException("Extraction produced empty content.")

    if resolved_format == "html":
        if not result.html.strip():
            raise click.ClickException("Extraction produced empty HTML content.")
        final_output = _html_document(result, source)
    else:
        final_output = _markdown_document(result, source)

    target = _resolve_output_path(
        output,
        result.title,
        source,
        _FORMAT_EXTENSIONS[resolved_format],
    )
    target.write_text(final_output, encoding="utf-8")
    log(f"[bold green]Saved[/] {target}")


if __name__ == "__main__":
    main()
