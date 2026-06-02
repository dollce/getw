from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from mark2down.cli import main
from mark2down.sources import ContentResult


def _url_result() -> ContentResult:
    return ContentResult(
        source="https://example.com/post",
        source_type="url",
        markdown="# Example Post\n\nUseful body text.\n",
        html="<article><h1>Example Post</h1><p>Useful body text.</p></article>\n",
        title="Example Post",
        final_url="https://example.com/post",
        lang="en",
        canonical="https://example.com/post",
        meta={"description": "A useful example.", "og:site_name": "Example"},
        json_ld=[
            {
                "@type": "Article",
                "headline": "Example Post",
                "author": {"name": "Ada"},
                "datePublished": "2026-01-02T03:04:05Z",
            }
        ],
        status=200,
        mime_type="text/html",
        extension=".html",
    )


class CliOutputFormatTests(unittest.TestCase):
    def test_url_defaults_to_html(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(), patch(
            "mark2down.cli.convert_url", return_value=_url_result()
        ):
            result = runner.invoke(main, ["https://example.com/post"])

            self.assertEqual(result.exit_code, 0, result.output)
            outputs = list(Path(".").glob("*.html"))
            self.assertEqual(len(outputs), 1)
            content = outputs[0].read_text(encoding="utf-8")
            self.assertTrue(content.startswith("<!doctype html>"))
            self.assertIn('<script type="application/json" id="mark2down-metadata-json">', content)
            self.assertIn("<dt>Author</dt><dd>Ada</dd>", content)
            self.assertFalse(list(Path(".").glob("*.md")))

    def test_url_can_still_write_markdown(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(), patch(
            "mark2down.cli.convert_url", return_value=_url_result()
        ):
            result = runner.invoke(
                main,
                ["https://example.com/post", "--format", "markdown"],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            outputs = list(Path(".").glob("*.md"))
            self.assertEqual(len(outputs), 1)
            content = outputs[0].read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"))
            self.assertIn("# Example Post", content)

    def test_output_suffix_infers_format(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(), patch(
            "mark2down.cli.convert_url", return_value=_url_result()
        ):
            md_result = runner.invoke(
                main,
                ["https://example.com/post", "-o", "out.md"],
            )
            html_result = runner.invoke(
                main,
                ["https://example.com/post", "-o", "out.html"],
            )

            self.assertEqual(md_result.exit_code, 0, md_result.output)
            self.assertEqual(html_result.exit_code, 0, html_result.output)
            self.assertTrue(Path("out.md").read_text(encoding="utf-8").startswith("---\n"))
            self.assertTrue(
                Path("out.html").read_text(encoding="utf-8").startswith("<!doctype html>")
            )

    def test_conflicting_format_and_suffix_fails(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(), patch(
            "mark2down.cli.convert_url", return_value=_url_result()
        ):
            result = runner.invoke(
                main,
                ["https://example.com/post", "--format", "html", "-o", "out.md"],
            )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("Output suffix implies markdown", result.output)

    def test_local_html_defaults_to_html(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("source.html").write_text(
                """
                <!doctype html>
                <html lang="en">
                  <head>
                    <title>Local Page</title>
                    <meta name="description" content="Local description">
                  </head>
                  <body>
                    <article><h1>Local Page</h1><p>Local body.</p></article>
                  </body>
                </html>
                """,
                encoding="utf-8",
            )

            result = runner.invoke(main, ["source.html", "-o", "converted"])

            self.assertEqual(result.exit_code, 0, result.output)
            outputs = list(Path("converted").glob("*.html"))
            self.assertEqual(len(outputs), 1)
            content = outputs[0].read_text(encoding="utf-8")
            self.assertTrue(content.startswith("<!doctype html>"))
            self.assertIn("<dt>Description</dt><dd>Local description</dd>", content)

    def test_html_format_rejects_non_html_inputs(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("notes.txt").write_text("plain text", encoding="utf-8")

            result = runner.invoke(main, ["notes.txt", "--format", "html"])

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("HTML output is only supported", result.output)


if __name__ == "__main__":
    unittest.main()
