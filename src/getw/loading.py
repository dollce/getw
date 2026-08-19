"""Static and optional browser-backed page acquisition."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx
from charset_normalizer import from_bytes
from lxml import html as lxml_html

from .errors import CapabilityError, InputError, LoadError
from .models import ExtractionConfig


@dataclass(frozen=True, slots=True)
class LoadedPage:
    requested_url: str
    final_url: str
    html: str
    mode: str
    status: int | None
    content_type: str | None
    elapsed_ms: int
    headers: Mapping[str, str]


class PageLoader(Protocol):
    def load(self, url: str, config: ExtractionConfig) -> LoadedPage: ...


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputError("URL input must be an absolute http:// or https:// URL.")


def _decode(content: bytes, charset: str | None) -> str:
    if charset:
        try:
            return content.decode(charset, errors="replace")
        except LookupError:
            pass
    match = from_bytes(content).best()
    return (
        str(match) if match is not None else content.decode("utf-8", errors="replace")
    )


def _possible_challenge_response(html: str, status: int) -> bool:
    if status not in {403, 429, 503}:
        return False
    lower = html.lower()
    return any(
        marker in lower
        for marker in (
            "just a moment",
            "checking your browser",
            "attention required",
            "cf-chl-",
            "challenge-platform",
            "captcha-delivery.com",
            "_incapsula_resource",
        )
    )


class HttpLoader:
    """Standards-compliant, certificate-verifying HTTP loader."""

    def load(self, url: str, config: ExtractionConfig) -> LoadedPage:
        validate_url(url)
        headers = {
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.8",
            **dict(config.headers),
        }
        started = time.monotonic()
        try:
            with (
                httpx.Client(
                    headers=headers,
                    follow_redirects=True,
                    timeout=httpx.Timeout(config.timeout),
                ) as client,
                client.stream("GET", url) as response,
            ):
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > config.max_response_bytes:
                        raise LoadError(
                            f"Response exceeds {config.max_response_bytes} bytes."
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                content_type = response.headers.get("content-type")
                media_type = (content_type or "").split(";", 1)[0].strip().lower()
                if media_type and not (
                    media_type.startswith("text/")
                    or media_type in {"application/xhtml+xml", "application/xml"}
                ):
                    raise LoadError(
                        f"URL returned unsupported content type {media_type!r}; "
                        "getw 2 extracts web text/HTML."
                    )
                html = _decode(content, response.charset_encoding)
                if response.status_code >= 400 and not _possible_challenge_response(
                    html, response.status_code
                ):
                    raise LoadError(
                        f"URL returned HTTP {response.status_code}: "
                        f"{response.reason_phrase}."
                    )
                return LoadedPage(
                    requested_url=url,
                    final_url=str(response.url),
                    html=html,
                    mode="static",
                    status=response.status_code,
                    content_type=content_type,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                )
        except LoadError:
            raise
        except httpx.HTTPError as exc:
            raise LoadError(f"Failed to load {url!r}: {exc}") from exc


_STABILITY_SCRIPT = """
() => {
  const body = document.body;
  if (!body) return [document.readyState, 0, 0, 0];
  const text = body.innerText || "";
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  }
  return [document.readyState, text.length, body.querySelectorAll("*").length, hash >>> 0];
}
"""


class PlaywrightLoader:
    """Lazy Playwright adapter; importing getw never imports Playwright."""

    def _wait_until_stable(
        self,
        page: object,
        config: ExtractionConfig,
        *,
        selector_satisfied: bool,
    ) -> None:
        if config.settle_timeout <= 0:
            return
        started = time.monotonic()
        deadline = started + config.settle_timeout
        last_signature: object | None = None
        stable_since: float | None = None
        changed = selector_satisfied

        while time.monotonic() < deadline:
            signature = page.evaluate(_STABILITY_SCRIPT)  # type: ignore[attr-defined]
            now = time.monotonic()
            if last_signature is not None and signature != last_signature:
                changed = True
            if signature == last_signature:
                stable_since = stable_since or now
            else:
                stable_since = now
                last_signature = signature

            observed_for = now - started
            stable_for = now - (stable_since or now)
            text_length = int(signature[1]) if signature else 0
            meaningful = (
                selector_satisfied or changed or text_length >= config.min_content_chars
            )
            if meaningful and observed_for >= 0.5 and stable_for >= config.stable_for:
                return
            # A no-mutation short shell is deliberately observed for the full
            # settle window. This avoids returning before its initial API call.
            page.wait_for_timeout(200)  # type: ignore[attr-defined]

    def load(self, url: str, config: ExtractionConfig) -> LoadedPage:
        validate_url(url)
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CapabilityError(
                "dom_rendering",
                "DOM rendering was required but Playwright is not installed.",
                "pip install 'getw[browser]'",
            ) from exc

        started = time.monotonic()
        timeout_ms = max(1, round(config.timeout * 1000))
        launch_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = None
            if config.browser_channel:
                launch_channels: list[str | None] = [config.browser_channel]
            else:
                # Prefer Playwright's pinned Chromium, then use system Chrome or
                # Edge when only the Python package is installed.
                launch_channels = [None, "chrome", "msedge"]
            for channel in launch_channels:
                label = channel or "chromium"
                try:
                    if channel is None:
                        browser = playwright.chromium.launch(headless=config.headless)
                    else:
                        browser = playwright.chromium.launch(
                            headless=config.headless,
                            channel=channel,
                        )
                    break
                except PlaywrightError as exc:
                    launch_errors.append(f"{label}: {str(exc).splitlines()[0]}")
            if browser is None:
                detail = "; ".join(launch_errors)
                raise CapabilityError(
                    "dom_rendering",
                    f"No usable Chromium browser was found ({detail}).",
                    "python -m playwright install chromium",
                )

            try:
                context = browser.new_context(
                    user_agent=config.user_agent,
                    extra_http_headers=dict(config.headers),
                )
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                selector_satisfied = False
                if config.wait_for:
                    page.locator(config.wait_for).first.wait_for(
                        state="visible", timeout=timeout_ms
                    )
                    selector_satisfied = True
                self._wait_until_stable(
                    page,
                    config,
                    selector_satisfied=selector_satisfied,
                )
                result = LoadedPage(
                    requested_url=url,
                    final_url=page.url,
                    html=page.content(),
                    mode="browser",
                    status=response.status if response is not None else None,
                    content_type=(
                        response.header_value("content-type")
                        if response is not None
                        else None
                    ),
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    headers={},
                )
                context.close()
                return result
            except PlaywrightTimeoutError as exc:
                raise LoadError(f"Timed out while rendering {url!r}: {exc}") from exc
            except PlaywrightError as exc:
                raise LoadError(f"Failed to render {url!r}: {exc}") from exc
            finally:
                browser.close()


_DYNAMIC_MARKERS: tuple[tuple[str, str], ...] = (
    ('id="__next"', "nextjs-root"),
    ("id='__next'", "nextjs-root"),
    ('id="__nuxt"', "nuxt-root"),
    ("id='__nuxt'", "nuxt-root"),
    ('id="root"', "javascript-root"),
    ("id='root'", "javascript-root"),
    ('id="app"', "javascript-root"),
    ("id='app'", "javascript-root"),
    ("data-reactroot", "react-root"),
    ("__next_data__", "nextjs-data"),
    ("_next/static", "nextjs-assets"),
    ("ng-version", "angular-root"),
    ("__nuxt__", "nuxt-data"),
    ("webpack", "webpack-bundle"),
    ("enable javascript", "javascript-required-message"),
)


def _visible_text_length(html: str) -> int:
    try:
        root = lxml_html.fromstring(html)
    except (ValueError, TypeError):
        return 0
    for element in root.xpath("//script|//style|//template|//noscript"):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    return len(re.sub(r"\s+", " ", root.text_content()).strip())


def rendering_reason(
    html: str,
    *,
    extracted_chars: int,
    min_content_chars: int,
    wait_for: str | None,
) -> str | None:
    """Return a version-stable reason for escalating static HTML to a DOM."""

    if wait_for:
        return "explicit-selector"
    if extracted_chars >= min_content_chars:
        return None
    lower = html.lower()
    for marker, reason in _DYNAMIC_MARKERS:
        if marker in lower:
            return reason
    if extracted_chars == 0:
        return "empty-extraction"
    if _visible_text_length(html) < min_content_chars and "<script" in lower:
        return "script-heavy-empty-shell"
    return None


_CHALLENGE_TITLES: tuple[tuple[str, str], ...] = (
    ("just a moment", "Cloudflare"),
    ("attention required", "Cloudflare"),
    ("checking your browser", "Cloudflare"),
    ("access denied", "Cloudflare/Akamai"),
    ("pardon our interruption", "Imperva"),
    ("one moment, please", "Akamai"),
)
_CHALLENGE_HTML = (
    "cf-chl-",
    "challenge-platform",
    "/cdn-cgi/challenge-platform/",
    "ct.captcha-delivery.com",
    "_incapsula_resource",
)


def challenge_vendor(html: str, title: str | None, status: int | None) -> str | None:
    title_lower = (title or "").lower()
    html_lower = html.lower()
    for marker, vendor in _CHALLENGE_TITLES:
        if marker not in title_lower:
            continue
        if any(value in html_lower for value in _CHALLENGE_HTML) or status in {
            403,
            429,
            503,
        }:
            return vendor
    return None
