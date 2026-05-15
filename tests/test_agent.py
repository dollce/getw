from __future__ import annotations

import unittest
from unittest.mock import patch

from mark2down import agent
from mark2down.ocr import OcrOptions


class _FakeResponse:
    status = 200


class _FakePage:
    def __init__(
        self,
        *,
        html: str = "<html><head><title>Loaded</title></head><body>Body</body></html>",
        title: str = "Loaded",
        status: int = 200,
    ) -> None:
        self._html = html
        self._title = title
        self._status = status
        self.url = "https://example.test/final"
        self.calls: list[tuple[str, object]] = []

    def goto(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("goto", (url, kwargs)))
        response = _FakeResponse()
        response.status = self._status
        return response

    def wait_for_load_state(self, state: str, **kwargs: object) -> None:
        self.calls.append(("wait_for_load_state", (state, kwargs)))

    def wait_for_selector(self, selector: str, **kwargs: object) -> None:
        self.calls.append(("wait_for_selector", (selector, kwargs)))

    def wait_for_timeout(self, timeout: int) -> None:
        self.calls.append(("wait_for_timeout", timeout))

    def evaluate(self, script: str, *args: object) -> object:
        self.calls.append(("evaluate", script))
        if script == agent.META_EXTRACTOR:
            return {
                "title": self._title,
                "lang": "en",
                "canonical": "https://example.test/canonical",
                "meta": {"description": "example"},
                "jsonLd": [{"@type": "Article"}],
            }
        return {"ok": True}

    def content(self) -> str:
        self.calls.append(("content", None))
        return self._html


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class AgentFetchTests(unittest.TestCase):
    def test_fetch_uses_cloakbrowser_context_and_preserves_metadata(self) -> None:
        page = _FakePage()
        context = _FakeContext(page)

        with patch("mark2down.agent._launch_stealth_context", return_value=context) as launch:
            result = agent.fetch(
                "https://example.test/source",
                wait_seconds=0.5,
                wait_selector=".ready",
                timeout_ms=1_234,
                user_agent="Custom UA",
                extra_http_headers={"X-Test": "1"},
                ocr=OcrOptions(enabled=False),
            )

        launch.assert_called_once_with(
            headless=True,
            humanize=True,
            user_agent="Custom UA",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={"X-Test": "1"},
            ignore_https_errors=True,
        )
        self.assertTrue(context.closed)
        self.assertEqual(result.url, "https://example.test/source")
        self.assertEqual(result.final_url, "https://example.test/final")
        self.assertEqual(result.title, "Loaded")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.lang, "en")
        self.assertEqual(result.meta["description"], "example")
        self.assertEqual(result.json_ld, [{"@type": "Article"}])
        self.assertIn(("wait_for_selector", (".ready", {"timeout": 1_234})), page.calls)
        self.assertIn(("evaluate", agent.DOM_INJECTION), page.calls)

    def test_fetch_closes_context_when_challenge_page_is_detected(self) -> None:
        page = _FakePage(
            html="<html><body>cf-chl-token</body></html>",
            title="Just a moment...",
            status=503,
        )
        context = _FakeContext(page)

        with patch("mark2down.agent._launch_stealth_context", return_value=context):
            with self.assertRaises(agent.ChallengePageError):
                agent.fetch(
                    "https://example.test/protected",
                    wait_seconds=0,
                    ocr=OcrOptions(enabled=False),
                )

        self.assertTrue(context.closed)


if __name__ == "__main__":
    unittest.main()
