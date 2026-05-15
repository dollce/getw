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

import re
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
    "[class*='blog-tags' i]",
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
    "[role='search']",
    "[aria-label~='search' i]",
    ".search",
    ".search-box",
    ".searchbox",
    ".site-search",
    "[class*='__search' i]",
    "[class*='searchbox' i]",
    "[id*='searchbox' i]",
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
    ".table-of-content",
    ".table-content-wrapper",
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
    "[class*='article-body' i]",
    "[class*='article-content' i]",
    ".post",
    ".article",
    "[class*='post-content' i]",
    "[class*='entry-content' i]",
    "[class*='blog-content' i]",
    "[class*='story-content' i]",
    "[class*='scrolling-content' i]",
    "#mw-content-text",  # Wikipedia
)

_NOISE_CLASS_ID_RE = re.compile(
    r"(?:^|[\s_-])("
    r"ad|advert|breadcrumb|comment|cookie|footer|gdpr|menu|modal|nav|"
    r"newsletter|pagination|promo|recommend|related|search|share|sidebar|"
    r"social|subscribe|tag|toc"
    r")(?:$|[\s_-])",
    re.IGNORECASE,
)

_HARD_NOISE_CLASS_ID_RE = re.compile(
    r"(?:^|[\s_-])(footer|navigation|related)(?:$|[\s_-])",
    re.IGNORECASE,
)

_CONTENT_CONTEXT_MARKERS = (
    "entry-content",
    "post-content",
    "article-content",
    "article-body",
    "blog-content",
    "story-content",
    "scrolling-content",
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


def _class_id_text(el: Tag) -> str:
    values: list[str] = []
    for attr in ("id", "class", "role", "aria-label"):
        value = el.get(attr)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return " ".join(values)


def _link_text_length(el: Tag) -> int:
    return sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))


def _strip_noise_markers(soup: BeautifulSoup) -> None:
    """Remove chrome whose class/id tokens identify known non-article modules."""
    structural_roots = {"html", "body", "main", "article"}
    for el in list(soup.find_all(True)):
        if el.parent is None or el.name in structural_roots:
            continue
        if _HARD_NOISE_CLASS_ID_RE.search(_class_id_text(el)):
            el.decompose()


def _is_content_context(el: Tag) -> bool:
    class_id = _class_id_text(el).lower()
    return (
        el.name in {"article", "main"}
        or el.get("role") == "main"
        or any(marker in class_id for marker in _CONTENT_CONTEXT_MARKERS)
    )


def _candidate_score(el: Tag) -> float:
    text = el.get_text(" ", strip=True)
    text_len = len(text)
    if text_len == 0:
        return 0.0

    link_density = _link_text_length(el) / max(text_len, 1)
    paragraph_count = len(el.find_all("p"))
    heading_count = len(el.find_all(["h1", "h2", "h3"]))

    score = float(text_len)
    score += min(paragraph_count, 40) * 80
    score += min(heading_count, 20) * 35
    score *= max(0.15, 1.0 - min(link_density, 0.95))

    name = el.name.lower()
    class_id = _class_id_text(el).lower()
    if name == "article" or "article" in class_id:
        score += 1200
    if name == "main" or el.get("role") == "main":
        score += 900
    if any(marker in class_id for marker in _CONTENT_CONTEXT_MARKERS[:4]):
        score += 1000
    if any(marker in class_id for marker in _CONTENT_CONTEXT_MARKERS[4:]):
        score += 700
    if _NOISE_CLASS_ID_RE.search(class_id):
        score *= 0.2
    if name in {"html", "body"}:
        score *= 0.05
    return score


def _pick_main(soup: BeautifulSoup) -> Tag:
    candidates: list[Tag] = []
    for sel in _CONTENT_SELECTORS:
        for el in soup.select(sel):
            if isinstance(el, Tag) and el.name not in {"html", "body"}:
                candidates.append(el)
    if candidates:
        return max(candidates, key=_candidate_score)
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


def _shares_content_context(heading: Tag, main: Tag) -> bool:
    main_ancestor_ids = {id(main), *(id(parent) for parent in main.parents if isinstance(parent, Tag))}
    for parent in heading.parents:
        if isinstance(parent, Tag) and id(parent) in main_ancestor_ids and _is_content_context(parent):
            return True
    return False


def _is_noise_heading(heading: Tag, main: Tag) -> bool:
    shared_content_context = _shares_content_context(heading, main)
    for parent in heading.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"header", "nav", "footer", "aside"} and not shared_content_context:
            return True
        if _NOISE_CLASS_ID_RE.search(_class_id_text(parent)):
            return True
    return False


def _nearby_heading_scopes(main: Tag) -> list[Tag]:
    """Return only the selected node and its content-context ancestors."""
    scopes = [main]
    node: Tag | None = main
    hops = 0
    while isinstance(node, Tag) and node.name not in {"body", "html"} and hops < 6:
        parent = node.parent
        if isinstance(parent, Tag) and _is_content_context(parent):
            scopes.append(parent)
        node = parent if isinstance(parent, Tag) else None
        hops += 1
    return scopes


def _prepend_page_heading_if_missing(working: BeautifulSoup, main: Tag) -> None:
    """Preserve a page-level H1 when the selected body starts below the hero."""
    if working.find("h1"):
        return

    headings: list[str] = []
    for scope in _nearby_heading_scopes(main):
        for heading in scope.find_all("h1"):
            if not _shares_content_context(heading, main):
                continue
            if _is_noise_heading(heading, main):
                continue
            text = " ".join(heading.get_text(" ", strip=True).split())
            if text:
                headings.append(text)
    unique_headings = list(dict.fromkeys(headings))
    if len(unique_headings) != 1:
        return

    target = working.find("body") or working
    h1 = working.new_tag("h1")
    h1.string = unique_headings[0]
    if target.contents:
        target.insert(0, h1)
    else:
        target.append(h1)


def html_to_markdown(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    _strip_tags(soup, _STRIP_TAGS)
    _strip_selectors(soup, _NOISE_SELECTORS)
    _strip_noise_markers(soup)

    main = _pick_main(soup)

    # Work on a detached copy so pruning doesn't affect the source soup.
    working = BeautifulSoup(str(main), "lxml")
    _strip_tags(working, _STRIP_TAGS)
    _strip_selectors(working, _NOISE_SELECTORS)
    _strip_noise_markers(working)
    _prepend_page_heading_if_missing(working, main)
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
