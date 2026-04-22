"""Agent-browser: Playwright-driven fetcher with DOM injection.

The browser acts as an agent — it loads the page, injects JavaScript to
expand collapsed sections, trigger lazy-loaded content via auto-scroll,
strip obvious noise, and finally hand back a settled HTML snapshot plus
metadata scraped from the live DOM.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# JavaScript injected into the page after initial load.  It removes inert
# noise, expands hidden content, rewires lazy-load images, and scrolls the
# page so infinite-scroll listeners fire.
DOM_INJECTION = r"""
async () => {
  const removeSelectors = [
    'script', 'style', 'noscript', 'template',
    'iframe[src*="doubleclick"]', 'iframe[src*="googletagmanager"]',
    '[aria-hidden="true"][role="presentation"]',
    '.cookie-banner', '.cookie-consent', '.gdpr',
    '.newsletter-signup', '.subscribe-popup',
    '[class*="ad-container"]', '[id*="google_ads"]',
  ];
  for (const sel of removeSelectors) {
    document.querySelectorAll(sel).forEach(el => el.remove());
  }

  // Expand <details> and [aria-expanded="false"] togglers.
  document.querySelectorAll('details').forEach(el => { el.open = true; });
  document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
    try { el.setAttribute('aria-expanded', 'true'); } catch (e) {}
  });

  // Promote lazy-loaded images so they appear in the final HTML.
  document.querySelectorAll('img').forEach(img => {
    const candidates = [
      img.getAttribute('data-src'),
      img.getAttribute('data-original'),
      img.getAttribute('data-lazy-src'),
      img.getAttribute('data-srcset'),
    ].filter(Boolean);
    if (candidates.length && (!img.src || img.src.startsWith('data:'))) {
      img.src = candidates[0];
    }
    img.loading = 'eager';
  });

  // Auto-scroll until the document stops growing (bounded to avoid loops).
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let last = 0;
  let current = document.documentElement.scrollHeight;
  let attempts = 0;
  while (current !== last && attempts < 40) {
    last = current;
    window.scrollTo(0, current);
    await sleep(250);
    current = document.documentElement.scrollHeight;
    attempts += 1;
  }
  window.scrollTo(0, 0);
  await sleep(200);
  return { scrolls: attempts, finalHeight: current };
}
"""

META_EXTRACTOR = r"""
() => {
  const meta = {};
  document.querySelectorAll('meta').forEach(el => {
    const key = el.getAttribute('name') || el.getAttribute('property') || el.getAttribute('itemprop');
    const val = el.getAttribute('content');
    if (key && val) meta[key.toLowerCase()] = val;
  });
  const jsonLd = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(el => {
    try { jsonLd.push(JSON.parse(el.textContent)); } catch (e) {}
  });
  const canonical = document.querySelector('link[rel="canonical"]');
  const lang = document.documentElement.getAttribute('lang') || '';
  return {
    meta,
    jsonLd,
    canonical: canonical ? canonical.getAttribute('href') : null,
    lang: lang,
    title: document.title || '',
  };
}
"""


@dataclass
class FetchResult:
    url: str
    final_url: str
    title: str
    html: str
    lang: str
    canonical: str | None
    meta: dict[str, str]
    json_ld: list[Any] = field(default_factory=list)
    status: int | None = None


class ChallengePageError(RuntimeError):
    """Raised when the response looks like an interactive bot-challenge page
    (Cloudflare Turnstile, Akamai bot manager, etc.) rather than real content."""

    def __init__(self, url: str, title: str, status: int | None, vendor: str) -> None:
        self.url = url
        self.title = title
        self.status = status
        self.vendor = vendor
        super().__init__(
            f"Bot challenge detected ({vendor}): title={title!r} status={status}"
        )


# Challenge-page titles observed in the wild. Matched case-insensitively as
# substrings so we tolerate minor variations ("Just a moment..." vs. "Just a
# moment").
_CHALLENGE_TITLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("just a moment", "Cloudflare"),
    ("attention required", "Cloudflare"),
    ("checking your browser", "Cloudflare"),
    ("access denied", "Cloudflare/Akamai"),
    ("pardon our interruption", "Imperva"),
    ("one moment, please", "Akamai"),
)

# HTML substrings that corroborate a challenge page when combined with a
# suspicious title + very short extractable body.
_CHALLENGE_HTML_MARKERS: tuple[str, ...] = (
    "cf-chl-",
    "challenge-platform",
    "cf_chl_opt",
    "cf-mitigated",
    "/cdn-cgi/challenge-platform/",
    "ct.captcha-delivery.com",
    "_Incapsula_Resource",
)


def _looks_like_challenge(
    *,
    title: str,
    html: str,
    status: int | None,
) -> str | None:
    """Return a vendor string if *html*/*title* looks like a bot-challenge
    interstitial, otherwise None."""
    title_lc = (title or "").lower()
    vendor: str | None = None
    for marker, v in _CHALLENGE_TITLE_MARKERS:
        if marker in title_lc:
            vendor = v
            break
    if vendor is None:
        return None
    # Require corroboration: either a known challenge fingerprint in the HTML
    # or a non-200 status. A legitimate page titled "Attention required" (e.g.
    # a blog post) shouldn't false-positive.
    html_lc = html.lower() if html else ""
    if any(m in html_lc for m in _CHALLENGE_HTML_MARKERS):
        return vendor
    if status is not None and status in (403, 429, 503):
        return vendor
    return None


def _install_browsers_if_needed() -> None:
    """Install Chromium the first time the tool is invoked on a machine."""
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    subprocess.run(cmd, check=True)


def fetch(
    url: str,
    *,
    wait_seconds: float = 1.0,
    wait_selector: str | None = None,
    timeout_ms: int = 45_000,
    user_agent: str = DEFAULT_USER_AGENT,
    headless: bool = True,
    extra_http_headers: dict[str, str] | None = None,
) -> FetchResult:
    """Load *url* in a headless browser and return settled HTML + metadata."""
    try:
        from playwright.sync_api import Error as PWError  # type: ignore
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required. Install with `uv tool install mark2down`"
            " or `pip install playwright`."
        ) from exc

    def _run() -> FetchResult:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                extra_http_headers=extra_http_headers or {},
                ignore_https_errors=True,
            )
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PWError:
                pass
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except PWError:
                    pass
            if wait_seconds:
                page.wait_for_timeout(int(wait_seconds * 1000))

            try:
                page.evaluate(DOM_INJECTION)
            except PWError:
                pass
            page.wait_for_timeout(300)

            meta_payload = page.evaluate(META_EXTRACTOR)
            html = page.content()
            final_url = page.url
            status = response.status if response else None
            browser.close()

            title = (meta_payload.get("title") or "").strip()
            vendor = _looks_like_challenge(title=title, html=html, status=status)
            if vendor is not None:
                raise ChallengePageError(url=final_url, title=title, status=status, vendor=vendor)

            return FetchResult(
                url=url,
                final_url=final_url,
                title=title,
                html=html,
                lang=(meta_payload.get("lang") or "").strip(),
                canonical=meta_payload.get("canonical"),
                meta=meta_payload.get("meta") or {},
                json_ld=meta_payload.get("jsonLd") or [],
                status=status,
            )

    try:
        return _run()
    except Exception as exc:
        msg = str(exc).lower()
        if "executable doesn't exist" in msg or "install" in msg and "chromium" in msg:
            print("[mark2down] Installing Chromium (first-time setup)...", file=sys.stderr)
            _install_browsers_if_needed()
            return _run()
        raise
