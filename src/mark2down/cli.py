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
from .agent import ChallengePageError, fetch
from .cleaner import clean_markdown
from .converter import html_to_markdown
from .metadata import build_frontmatter

console = Console(stderr=True)


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
    output_dir: Path | None,
    filename: str | None,
    title: str,
    url: str,
) -> Path:
    target_dir = Path(output_dir) if output_dir else Path.cwd()
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    name = filename or _default_filename(title, url)
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
    epilog="Example:\n  mark2down https://example.com/post -o ./notes --stdout",
)
@click.version_option(__version__, "-V", "--version", prog_name="mark2down")
@click.argument("url")
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to save the Markdown file (default: current working directory).",
)
@click.option(
    "-f",
    "--filename",
    type=str,
    default=None,
    help="Override the output filename (default: slug of the page title).",
)
@click.option(
    "-p",
    "--stdout",
    "emit_stdout",
    is_flag=True,
    default=False,
    help="Also print the final Markdown to stdout.",
)
@click.option(
    "--no-save",
    is_flag=True,
    default=False,
    help="Do not write a file; just print Markdown to stdout.",
)
@click.option(
    "--no-frontmatter",
    is_flag=True,
    default=False,
    help="Skip the YAML frontmatter block.",
)
@click.option(
    "--wait",
    type=float,
    default=1.0,
    show_default=True,
    help="Extra seconds to wait after networkidle before DOM injection.",
)
@click.option(
    "--wait-selector",
    type=str,
    default=None,
    help="CSS selector that must appear before extraction (e.g. 'article').",
)
@click.option(
    "--timeout",
    type=int,
    default=45,
    show_default=True,
    help="Navigation/network timeout in seconds.",
)
@click.option(
    "--no-headless",
    is_flag=True,
    default=False,
    help="Launch the browser with a visible window (debug).",
)
@click.option(
    "--user-agent",
    type=str,
    default=None,
    help="Override the default Chromium user agent string.",
)
@click.option(
    "--header",
    "headers",
    multiple=True,
    help="Extra HTTP header (repeatable). Format: 'Name: value'.",
)
@click.option(
    "--install-browsers",
    is_flag=True,
    default=False,
    help="Install the Chromium browser binary and exit.",
)
@click.option("-q", "--quiet", is_flag=True, default=False, help="Silence progress messages.")
def main(
    url: str,
    output_dir: Path | None,
    filename: str | None,
    emit_stdout: bool,
    no_save: bool,
    no_frontmatter: bool,
    wait: float,
    wait_selector: str | None,
    timeout: int,
    no_headless: bool,
    user_agent: str | None,
    headers: tuple[str, ...],
    install_browsers: bool,
    quiet: bool,
) -> None:
    """Fetch URL, convert the page to clean Markdown, and (by default) save it."""
    if install_browsers:
        from .agent import _install_browsers_if_needed

        _install_browsers_if_needed()
        click.echo("Chromium installed.")
        return

    if not url.startswith(("http://", "https://")):
        raise click.BadParameter("URL must start with http:// or https://")

    extra_headers: dict[str, str] = {}
    for raw in headers:
        if ":" not in raw:
            raise click.BadParameter(f"Header must be 'Name: value', got: {raw!r}")
        name, _, val = raw.partition(":")
        extra_headers[name.strip()] = val.strip()

    log = console.log if not quiet else (lambda *_args, **_kw: None)

    log(f"[bold cyan]Fetching[/] {url}")
    try:
        result = fetch(
            url,
            wait_seconds=wait,
            wait_selector=wait_selector,
            timeout_ms=timeout * 1000,
            user_agent=user_agent or None,  # type: ignore[arg-type]
            headless=not no_headless,
            extra_http_headers=extra_headers or None,
        )
    except ChallengePageError as exc:
        # Silent failure is worse than loud failure here: without this the
        # challenge interstitial gets serialized as if it were the real page.
        raise click.ClickException(
            f"Bot challenge page detected ({exc.vendor}): title={exc.title!r} "
            f"status={exc.status}. mark2down cannot solve interactive challenges "
            f"in headless mode.\n"
            f"Workaround: solve the challenge once in a regular browser, copy "
            f"the session cookie (e.g. cf_clearance for Cloudflare), then retry "
            f"with:\n"
            f"  m2d {url!r} --header 'Cookie: cf_clearance=...'"
        ) from exc
    log(f"  status={result.status} lang={result.lang or '?'} title={result.title!r}")

    log("[bold cyan]Converting[/] to Markdown")
    markdown_body = html_to_markdown(result.html, result.final_url)
    markdown_body = clean_markdown(markdown_body)

    if not markdown_body.strip():
        raise click.ClickException("Extraction produced empty content. Try --wait or --wait-selector.")

    if no_frontmatter:
        final_md = markdown_body
    else:
        frontmatter = build_frontmatter(
            url=url,
            final_url=result.final_url,
            title=result.title,
            html_lang=result.lang,
            canonical=result.canonical,
            meta=result.meta,
            json_ld=result.json_ld,
            markdown=markdown_body,
        )
        final_md = frontmatter + markdown_body

    wrote_file = False
    if not no_save:
        target = _resolve_output_path(output_dir, filename, result.title, url)
        target.write_text(final_md, encoding="utf-8")
        wrote_file = True
        log(f"[bold green]Saved[/] {target}")

    should_print = emit_stdout or no_save or not wrote_file
    if should_print:
        sys.stdout.write(final_md)
        if not final_md.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
