"""Command-line interface: content on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import GetwError
from .extraction import extract
from .models import Html, LoadMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="getw",
        description="Extract a web page into source-grounded, LLM-ready text or semantic JSON.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        default="-",
        help="Absolute http(s) URL. Use - with --html to read HTML from stdin.",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Treat SOURCE as an HTML file (or read HTML from stdin when SOURCE is -).",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL used to resolve links in supplied HTML.",
    )
    parser.add_argument(
        "--format",
        choices=("compact", "markdown", "plain", "json"),
        default="compact",
        help="Output projection (default: compact).",
    )
    parser.add_argument(
        "--render",
        choices=tuple(mode.value for mode in LoadMode),
        default=LoadMode.AUTO.value,
        help="URL loading mode (default: auto).",
    )
    parser.add_argument(
        "--wait-for",
        metavar="SELECTOR",
        help="In browser/auto mode, wait until this selector is visible.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Load timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--browser-channel",
        help="Playwright browser channel, for example chrome or msedge.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write output to a file instead of stdout.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print acquisition attempts to stderr.",
    )
    parser.add_argument("--version", action="version", version=f"getw {__version__}")
    return parser


def _html_source(value: str, base_url: str | None) -> Html:
    if value == "-":
        return Html(sys.stdin.buffer.read(), base_url=base_url)
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"HTML input is not a file: {path}")
    return Html(path.read_bytes(), base_url=base_url or path.resolve().as_uri())


def _emit(value: str, output: Path | None) -> None:
    if output is None:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
        sys.stdout.write(value)
        return
    target = output.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.source == "-" and not args.html:
        print(
            "getw: error: stdin input is HTML; pass --html when SOURCE is -",
            file=sys.stderr,
        )
        return 2

    try:
        source = _html_source(args.source, args.base_url) if args.html else args.source
        document = extract(
            source,
            render=args.render,
            wait_for=args.wait_for,
            timeout=args.timeout,
            browser_channel=args.browser_channel,
        )
        if args.format == "json":
            output = document.to_json(indent=2 if args.pretty else None) + "\n"
        else:
            output = document.render(args.format)
        _emit(output, args.output)

        for notice in document.notices:
            print(f"getw: {notice.severity}: {notice.message}", file=sys.stderr)
        if args.verbose:
            for attempt in document.attempts:
                selected = " selected" if attempt.selected else ""
                reason = f" reason={attempt.reason}" if attempt.reason else ""
                print(
                    f"getw: {attempt.mode}{selected} status={attempt.status} "
                    f"chars={attempt.extracted_chars} quality={attempt.quality:.3f} "
                    f"elapsed_ms={attempt.elapsed_ms}{reason}",
                    file=sys.stderr,
                )
        return 0
    except (GetwError, OSError, UnicodeError, ValueError) as exc:
        print(f"getw: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
