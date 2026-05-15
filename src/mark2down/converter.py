"""HTML → Markdown pipeline.

Trafilatura is excellent for metadata but mangles tables in its HTML/markdown
output (it rewrites `<tr>/<td>` into a custom `<row>/<cell>` schema).  Since
table fidelity is a hard requirement, we do our own main-content extraction:

1. Parse the full HTML with BeautifulSoup (lxml backend).
2. Drop inert tags (`script`, `style`, …) and known chrome (nav, footer,
   cookie banners, share buttons, ads).
3. Pick the main content container — prefer `<article>`, `<main>`,
   `[role='main']`, or the largest candidate div by text length.
4. Resolve relative `href`/`src` attributes so the Markdown stays usable.
5. Swap each `<table>` for a placeholder, render with our custom writer.
6. Run markdownify on the rest and re-inject the tables.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as to_md

from .tables import inject_tables, replace_tables_with_placeholders

_STRIP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "form",
    "input",
    "button",
    "canvas",
    "object",
    "embed",
)

_NOISE_SELECTORS = (
    "nav",
    "aside",
    "footer",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    "[role='complementary']",
    ".nav",
    ".navbar",
    ".menu",
    ".breadcrumb",
    ".breadcrumbs",
    ".pagination",
    ".sidebar",
    ".side-bar",
    ".site-header",
    ".site-footer",
    ".page-header",
    ".page-footer",
    ".comments",
    ".comment-list",
    ".comment-section",
    "#comments",
    ".entry-comments",
    ".comment-respond",
    ".related",
    ".related-posts",
    ".related-articles",
    ".article-footer",
    ".entry-footer",
    ".post-footer",
    ".article-tags",
    ".post-tags",
    ".tag-list",
    ".newsletter",
    ".subscribe",
    ".cookie",
    ".cookies",
    ".cookie-banner",
    ".gdpr",
    ".share",
    ".social-share",
    ".share-buttons",
    ".advertisement",
    ".advert",
    ".ads",
    "[class*='ad-slot']",
    "[class*='ad-banner']",
    "[id*='ad-slot']",
    "[aria-label*='breadcrumb' i]",
    "[aria-label*='navigation' i]",
    ".noprint",
    ".hatnote",
    ".mw-editsection",
    ".mw-editsection-bracket",
    ".mw-cite-backlink",
    ".mw-jump-link",
    ".mw-empty-elt",
    "sup.reference",
    "ol.references",
    ".citation-needed",
    ".printfooter",
    ".languages",
    "#p-lang",
    "[class*='portlet']",
    ".vector-language-dropdown",
    ".vector-menu",
    ".mw-portlet",
    ".ambox",
    ".navbox",
    ".vertical-navbox",
    "table[role='presentation']",
    "table.metadata",
    ".infobox-below",
    ".sistersitebox",
    ".thumbcaption .magnify",
    ".external.text",
    ".c-article__sidebar",
    ".c-article__footer",
    ".c-article__comments",
)

_CONTENT_SELECTORS = (
    "article",
    "main",
    "[role='main']",
    "#content",
    "#main-content",
    "#primary",
    ".main-content",
    ".content",
    ".post-content",
    ".entry-content",
    ".article-body",
    ".article-content",
    ".post",
    ".article",
    "#mw-content-text",  # Wikipedia
)


def _strip_selectors(soup: BeautifulSoup, selectors: tuple[str, ...]) -> None:
    for sel in selectors:
        try:
            for el in soup.select(sel):
                el.decompose()
        except NotImplementedError:
            continue


def _strip_tags(soup: BeautifulSoup, tag_names: tuple[str, ...]) -> None:
    for tag in soup.find_all(tag_names):
        tag.decompose()


def _pick_main(soup: BeautifulSoup) -> Tag:
    candidates: list[Tag] = []
    for sel in _CONTENT_SELECTORS:
        for el in soup.select(sel):
            if isinstance(el, Tag):
                candidates.append(el)
    if candidates:
        return max(candidates, key=lambda el: len(el.get_text(strip=True)))
    body = soup.find("body")
    return body if isinstance(body, Tag) else soup  # type: ignore[return-value]


def _resolve_urls(scope: Tag, base_url: str) -> None:
    for a in scope.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            a["href"] = urljoin(base_url, href)
    for img in scope.find_all("img"):
        src = (img.get("src") or "").strip()
        if src.startswith("data:"):
            # Base64 image payloads are hostile to LLM context windows. Keep
            # the alt text and replace the opaque binary payload with a stable
            # placeholder.
            img["src"] = "embedded-image"
            if not img.get("alt"):
                img["alt"] = ""
            continue
        if not src or src.startswith("data:"):
            for attr in ("data-src", "data-original", "data-lazy-src", "data-srcset"):
                v = img.get(attr)
                if v:
                    src = v.split(",")[0].strip().split(" ")[0]
                    break
        if src and not src.startswith("data:"):
            img["src"] = urljoin(base_url, src)
        if not img.get("alt"):
            img["alt"] = ""


def _prune_empty(scope: Tag) -> None:
    """Remove elements that contain no text, no images, and no tables."""
    # Iterate leaves-first by converting generator to list copy.
    for el in list(scope.find_all(True))[::-1]:
        if el.name in {"br", "hr", "img", "video", "audio", "td", "th"}:
            continue
        if el.find("img") or el.find("table") or el.find("pre") or el.find("code"):
            continue
        text = el.get_text(strip=True)
        if not text:
            el.decompose()


def html_to_markdown(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    _strip_tags(soup, _STRIP_TAGS)
    _strip_selectors(soup, _NOISE_SELECTORS)

    main = _pick_main(soup)

    # Work on a detached copy so pruning doesn't affect the source soup.
    working = BeautifulSoup(str(main), "lxml")
    _strip_tags(working, _STRIP_TAGS)
    _strip_selectors(working, _NOISE_SELECTORS)
    _resolve_urls(working, url)
    _prune_empty(working)

    tables_md = replace_tables_with_placeholders(working)

    md = to_md(
        str(working),
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
        newline_style="SPACES",
        escape_asterisks=False,
        escape_underscores=False,
        escape_misc=False,
        code_language="",
    )

    md = inject_tables(md, tables_md)
    return md
