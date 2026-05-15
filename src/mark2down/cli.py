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
from .metadata import build_frontmatter, build_source_frontmatter
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


def _default_filename(title: str, url: str) -> str:
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
    return base + ".md"


def _resolve_output_path(
    output: Path | None,
    title: str,
    url: str,
) -> Path:
    if output is None:
        target_dir = Path.cwd().resolve()
        name = _default_filename(title, url)
    else:
        target = output.expanduser()
        if target.exists() and target.is_file():
            target_dir = target.parent.resolve()
            name = target.name
        elif target.suffix.lower() == ".md":
            target_dir = target.parent.resolve()
            name = target.name
        else:
            target_dir = target.resolve()
            name = _default_filename(title, url)

    target_dir.mkdir(parents=True, exist_ok=True)
    if not name.endswith(".md"):
        name += ".md"
    path = target_dir / name
    # Avoid silent overwrite: append -N suffix when needed.
    counter = 2
    stem = path.stem
    while path.exists():
        path = target_dir / f"{stem}-{counter}.md"
        counter += 1
    return path


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "\b\nExamples:\n"
        "  m2d https://example.com/post\n"
        "  m2d ./report.pdf -o ./notes\n"
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
    help="Save path. Use a directory or a .md file path. Defaults to the current directory.",
)
def main(
    source: str | None,
    output: Path | None,
) -> None:
    """Convert a URL, local file, or stdin to clean Markdown."""
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

    log("[bold cyan]Converting[/] to Markdown")
    markdown_body = result.markdown

    if not markdown_body.strip():
        raise click.ClickException("Extraction produced empty content.")

    if result.source_type == "url":
        final_url = result.final_url or source
        assert source is not None
        frontmatter = build_frontmatter(
            url=source,
            final_url=final_url,
            title=result.title,
            html_lang=result.lang,
            canonical=result.canonical,
            meta=result.meta,
            json_ld=result.json_ld,
            markdown=markdown_body,
        )
        final_md = frontmatter + markdown_body
    else:
        frontmatter = build_source_frontmatter(
            source=result.source,
            source_type=result.source_type,
            title=result.title,
            markdown=markdown_body,
            path=result.path,
            mime_type=result.mime_type,
            extension=result.extension,
            charset=result.charset,
        )
        final_md = frontmatter + markdown_body

    target = _resolve_output_path(output, result.title, source)
    target.write_text(final_md, encoding="utf-8")
    log(f"[bold green]Saved[/] {target}")


if __name__ == "__main__":
    main()
