"""Adaptive loading and deterministic structural extraction."""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

import lxml.etree as etree  # noqa: PLR0402 -- lxml's type stubs expose this module path.
import lxml.html as lxml_html
from charset_normalizer import from_bytes
from trafilatura import bare_extraction, baseline
from trafilatura.settings import DEFAULT_CONFIG

from .errors import (
    CapabilityError,
    ChallengePageError,
    ContentNotFoundError,
    GetwError,
    InputError,
    LoadError,
)
from .loading import (
    HttpLoader,
    LoadedPage,
    PageLoader,
    PlaywrightLoader,
    challenge_vendor,
    rendering_reason,
)
from .models import (
    Document,
    ExtractionConfig,
    ExtractionMode,
    Html,
    LoadAttempt,
    LoadMode,
    Metadata,
    Node,
    Notice,
    SourceInfo,
)
from .rendering import grounding_text

_TRAFILATURA_MIN_CHARS = 10


def _decode_html(value: str | bytes, encoding: str | None) -> str:
    if isinstance(value, str):
        return value
    if encoding:
        try:
            return value.decode(encoding, errors="replace")
        except LookupError as exc:
            raise InputError(f"Unknown HTML encoding: {encoding}") from exc
    match = from_bytes(value).best()
    return str(match) if match is not None else value.decode("utf-8", errors="replace")


def _as_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _as_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json_value(item) for item in value]
    return str(value)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _raw_page_metadata(html: str, base_url: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": None,
        "language": None,
        "canonical": None,
        "meta": {},
        "json_ld": [],
    }
    try:
        root = lxml_html.fromstring(html)
    except (ValueError, TypeError, etree.ParserError):
        return result

    title = root.xpath("string(//title[1])")
    result["title"] = _clean(title)
    html_nodes = root.xpath("//html[1]")
    if html_nodes:
        result["language"] = _clean(html_nodes[0].get("lang"))

    meta: dict[str, str] = {}
    for element in root.xpath("//meta[@content]"):
        key = element.get("name") or element.get("property") or element.get("itemprop")
        value = _clean(element.get("content"))
        if key and value:
            meta[str(key).strip().lower()] = value
    result["meta"] = meta

    for element in root.xpath("//link[@href]"):
        rel = {part.lower() for part in str(element.get("rel") or "").split()}
        if "canonical" in rel:
            result["canonical"] = urljoin(base_url or "", element.get("href"))
            break

    json_ld: list[Any] = []
    for element in root.xpath(
        "//script[translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='application/ld+json']"
    ):
        raw = element.text or ""
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        json_ld.append(_as_json_value(value))
    result["json_ld"] = json_ld
    return result


def _json_ld_objects(values: list[Any]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            objects.append(value)
            graph = value.get("@graph")
            if graph is not None:
                visit(graph)

    visit(values)
    return objects


def _json_ld_authors(objects: list[dict[str, Any]]) -> tuple[str, ...]:
    authors: list[str] = []
    for value in objects:
        author = value.get("author")
        items = author if isinstance(author, list) else [author]
        for item in items:
            name = item.get("name") if isinstance(item, dict) else item
            cleaned = _clean(name)
            if cleaned and cleaned not in authors:
                authors.append(cleaned)
    return tuple(authors)


def _json_ld_pick(objects: list[dict[str, Any]], *keys: str) -> str | None:
    for value in objects:
        for key in keys:
            item = value.get(key)
            if isinstance(item, dict):
                item = item.get("name") or item.get("url")
            if isinstance(item, list) and item:
                item = item[0]
            cleaned = _clean(item)
            if cleaned:
                return cleaned
    return None


def _split_authors(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = [part.strip() for part in re.split(r"\s*(?:;|\|)\s*", value)]
    return tuple(dict.fromkeys(part for part in parts if part))


def _metadata_from(
    extracted: Any,
    raw: dict[str, Any],
    final_url: str | None,
) -> Metadata:
    meta: dict[str, str] = raw["meta"]
    json_ld: list[Any] = raw["json_ld"]
    objects = _json_ld_objects(json_ld)

    authors = _split_authors(_clean(getattr(extracted, "author", None)))
    if not authors:
        authors = _split_authors(
            meta.get("author")
            or meta.get("article:author")
            or meta.get("parsely-author")
        )
    if not authors:
        authors = _json_ld_authors(objects)

    canonical = raw["canonical"]
    extracted_url = _clean(getattr(extracted, "url", None))
    if not canonical and extracted_url and extracted_url != final_url:
        canonical = extracted_url

    title = (
        _clean(getattr(extracted, "title", None))
        or raw["title"]
        or meta.get("og:title")
        or _json_ld_pick(objects, "headline", "name")
    )
    description = (
        _clean(getattr(extracted, "description", None))
        or meta.get("description")
        or meta.get("og:description")
        or _json_ld_pick(objects, "description")
    )
    image = (
        _clean(getattr(extracted, "image", None))
        or meta.get("og:image")
        or _json_ld_pick(objects, "image")
    )
    if image:
        image = urljoin(final_url or "", image)

    categories = tuple(
        _clean(item) for item in getattr(extracted, "categories", ()) or ()
    )
    tags = tuple(_clean(item) for item in getattr(extracted, "tags", ()) or ())
    return Metadata(
        title=title,
        description=description,
        authors=authors,
        site_name=(
            _clean(getattr(extracted, "sitename", None))
            or meta.get("og:site_name")
            or _json_ld_pick(objects, "publisher")
        ),
        language=(
            _clean(getattr(extracted, "language", None))
            or raw["language"]
            or meta.get("og:locale")
        ),
        canonical_url=canonical,
        published_at=(
            _clean(getattr(extracted, "date", None))
            or meta.get("article:published_time")
            or meta.get("date")
            or _json_ld_pick(objects, "datePublished")
        ),
        modified_at=(
            meta.get("article:modified_time") or _json_ld_pick(objects, "dateModified")
        ),
        image=image,
        categories=tuple(item for item in categories if item),
        tags=tuple(item for item in tags if item),
        extra=_as_json_value({"html_meta": meta, "json_ld": json_ld}),
    )


def _normalize_text_leaf(value: str | None, *, preserve: bool = False) -> str | None:
    if value is None:
        return None
    if preserve:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return value if value else None
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value if value.strip() else None


class _TreeBuilder:
    def __init__(self) -> None:
        self.counter = 0

    def _id(self) -> str:
        self.counter += 1
        return f"n{self.counter:05d}"

    def _text_node(self, value: str | None, *, preserve: bool = False) -> Node | None:
        normalized = _normalize_text_leaf(value, preserve=preserve)
        if normalized is None:
            return None
        return Node(id=self._id(), kind="text", text=normalized)

    def _children(self, element: Any, *, preserve: bool = False) -> tuple[Node, ...]:
        children: list[Node] = []
        first = self._text_node(element.text, preserve=preserve)
        if first is not None:
            children.append(first)
        for child in element:
            converted = self.convert(child, parent_tag=str(element.tag))
            if converted is not None:
                children.append(converted)
            tail = self._text_node(child.tail, preserve=preserve)
            if tail is not None:
                children.append(tail)
        return tuple(children)

    def convert(self, element: Any, *, parent_tag: str = "") -> Node | None:
        tag = str(element.tag).lower()
        if tag == "head":
            rendition = str(element.get("rend") or "h2")
            match = re.search(r"([1-6])", rendition)
            level = int(match.group(1)) if match else 2
            return Node(
                id=self._id(),
                kind="heading",
                attrs={"level": level},
                children=self._children(element),
            )
        if tag in {"p", "ab"}:
            return Node(
                id=self._id(), kind="paragraph", children=self._children(element)
            )
        if tag == "list":
            return Node(
                id=self._id(),
                kind="list",
                attrs={"ordered": str(element.get("rend") or "").lower() == "ol"},
                children=self._children(element),
            )
        if tag == "item":
            return Node(
                id=self._id(), kind="list_item", children=self._children(element)
            )
        if tag == "quote":
            return Node(id=self._id(), kind="quote", children=self._children(element))
        if tag == "table":
            return Node(id=self._id(), kind="table", children=self._children(element))
        if tag == "row":
            return Node(
                id=self._id(), kind="table_row", children=self._children(element)
            )
        if tag == "cell":
            return Node(
                id=self._id(),
                kind="table_cell",
                attrs={"header": element.get("role") == "head"},
                children=self._children(element),
            )
        if tag == "graphic":
            source = element.get("src") or element.get("target") or ""
            return Node(
                id=self._id(),
                kind="image",
                attrs={
                    "url": source,
                    "alt": element.get("alt") or "",
                    "title": element.get("title") or "",
                },
            )
        if tag == "ref":
            return Node(
                id=self._id(),
                kind="link",
                attrs={"url": element.get("target") or ""},
                children=self._children(element),
            )
        if tag == "hi":
            rendition = str(element.get("rend") or "#i")
            kind = {
                "#b": "strong",
                "#i": "emphasis",
                "#u": "underline",
                "#t": "inline_code",
            }.get(rendition, "emphasis")
            return Node(id=self._id(), kind=kind, children=self._children(element))
        if tag == "lb":
            return Node(id=self._id(), kind="line_break")
        if tag == "code":
            if parent_tag in {"p", "head", "item", "cell", "quote", "hi", "ref"}:
                return Node(
                    id=self._id(),
                    kind="inline_code",
                    children=self._children(element, preserve=True),
                )
            nested = element.find("code")
            classes = str(
                (nested if nested is not None else element).get("class") or ""
            )
            language_match = re.search(r"(?:language-|lang-)([\w.+-]+)", classes)
            value = "".join(element.itertext())
            return Node(
                id=self._id(),
                kind="code_block",
                text=value,
                attrs={"language": language_match.group(1) if language_match else ""},
            )
        if tag == "del":
            return Node(
                id=self._id(), kind="strikethrough", children=self._children(element)
            )

        children = self._children(element)
        if children:
            return Node(id=self._id(), kind="group", children=children)
        return None

    def root(self, body: Any) -> Node:
        root_id = self._id()
        children = self._children(body)
        return Node(id=root_id, kind="document", children=children)


@dataclass(frozen=True, slots=True)
class _Candidate:
    page: LoadedPage
    root: Node
    metadata: Metadata
    extracted_chars: int
    quality: float


def _quality(root: Node) -> tuple[int, float]:
    grounded = grounding_text(root).text
    char_count = len(grounded)

    def walk(node: Node) -> list[Node]:
        result = [node]
        for child in node.children:
            result.extend(walk(child))
        return result

    nodes = walk(root)
    block_count = sum(
        node.kind
        in {"heading", "paragraph", "list_item", "quote", "code_block", "table"}
        for node in nodes
    )
    structure_count = sum(
        node.kind in {"heading", "list", "quote", "code_block", "table", "link"}
        for node in nodes
    )
    score = min(char_count / 1500, 0.65)
    score += min(block_count / 12, 0.2)
    score += min(structure_count / 8, 0.15)
    lower = grounded.lower()
    if any(
        marker in lower
        for marker in ("enable javascript", "checking your browser", "just a moment")
    ):
        score -= 0.35
    return char_count, round(max(0.0, min(1.0, score)), 4)


def _trafilatura_config() -> Any:
    config = deepcopy(DEFAULT_CONFIG)
    for key in (
        "MIN_EXTRACTED_SIZE",
        "MIN_OUTPUT_SIZE",
        "MIN_EXTRACTED_COMM_SIZE",
        "MIN_OUTPUT_COMM_SIZE",
    ):
        config.set("DEFAULT", key, str(_TRAFILATURA_MIN_CHARS))
    return config


def _extract_candidate(page: LoadedPage, config: ExtractionConfig) -> _Candidate:
    precision = config.extraction_mode is ExtractionMode.PRECISION
    recall = config.extraction_mode is ExtractionMode.RECALL
    try:
        extracted: Any = bare_extraction(
            page.html,
            url=page.final_url,
            output_format="python",
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_images=True,
            include_formatting=True,
            with_metadata=True,
            favor_precision=precision,
            favor_recall=recall,
            config=_trafilatura_config(),
        )
    except (ValueError, TypeError, AttributeError, etree.Error) as exc:
        raise ContentNotFoundError(
            f"Trafilatura could not parse the page: {exc}"
        ) from exc

    if extracted is None or extracted.body is None:
        try:
            body, text, _ = baseline(page.html)
        except (ValueError, TypeError, AttributeError, etree.Error) as exc:
            raise ContentNotFoundError("No extractable web content was found.") from exc
        if body is None or not _clean(text):
            raise ContentNotFoundError("No extractable web content was found.")
        extracted = SimpleNamespace(
            body=body,
            title=None,
            author=None,
            url=page.final_url,
            description=None,
            sitename=None,
            date=None,
            categories=(),
            tags=(),
            language=None,
            image=None,
        )

    root = _TreeBuilder().root(extracted.body)
    extracted_chars, quality = _quality(root)
    if extracted_chars == 0:
        raise ContentNotFoundError("Extraction produced no meaningful text.")
    raw = _raw_page_metadata(page.html, page.final_url)
    metadata = _metadata_from(extracted, raw, page.final_url)
    return _Candidate(
        page=page,
        root=root,
        metadata=metadata,
        extracted_chars=extracted_chars,
        quality=quality,
    )


def _attempt(candidate: _Candidate, reason: str | None = None) -> LoadAttempt:
    return LoadAttempt(
        mode=candidate.page.mode,  # type: ignore[arg-type]
        url=candidate.page.requested_url or None,
        final_url=candidate.page.final_url or None,
        status=candidate.page.status,
        elapsed_ms=candidate.page.elapsed_ms,
        html_chars=len(candidate.page.html),
        extracted_chars=candidate.extracted_chars,
        quality=candidate.quality,
        reason=reason,
    )


def _failed_attempt(page: LoadedPage, reason: str) -> LoadAttempt:
    return LoadAttempt(
        mode=page.mode,  # type: ignore[arg-type]
        url=page.requested_url or None,
        final_url=page.final_url or None,
        status=page.status,
        elapsed_ms=page.elapsed_ms,
        html_chars=len(page.html),
        extracted_chars=0,
        quality=0.0,
        reason=reason,
    )


def _is_obvious_shell(candidate: _Candidate, reason: str) -> bool:
    if reason in {"empty-extraction", "script-heavy-empty-shell"}:
        return True
    text = grounding_text(candidate.root).text.lower().strip()
    return candidate.extracted_chars < 80 and any(
        marker in text for marker in ("loading", "enable javascript", "please wait")
    )


class Extractor:
    """Small facade hiding loading, selection, and IR construction."""

    def __init__(
        self,
        config: ExtractionConfig | None = None,
        *,
        http_loader: PageLoader | None = None,
        browser_loader: PageLoader | None = None,
    ) -> None:
        self.config = config or ExtractionConfig()
        self.http_loader = http_loader or HttpLoader()
        self.browser_loader = browser_loader

    def _browser(self) -> PageLoader:
        return self.browser_loader or PlaywrightLoader()

    def _finalize(
        self,
        candidate: _Candidate,
        *,
        source_kind: str,
        requested_url: str | None,
        base_url: str | None,
        attempts: list[LoadAttempt],
        notices: list[Notice],
    ) -> Document:
        vendor = challenge_vendor(
            candidate.page.html,
            candidate.metadata.title,
            candidate.page.status,
        )
        if vendor:
            raise ChallengePageError(
                vendor,
                candidate.metadata.title or "",
                candidate.page.status,
            )
        selected_attempts = tuple(
            replace(
                attempt,
                selected=(
                    attempt.mode == candidate.page.mode
                    and attempt.final_url == (candidate.page.final_url or None)
                ),
            )
            for attempt in attempts
        )
        return Document(
            schema_version="1",
            source=SourceInfo(
                input_kind=source_kind,  # type: ignore[arg-type]
                requested_url=requested_url,
                final_url=candidate.page.final_url or None,
                base_url=base_url,
                loaded_via=candidate.page.mode,  # type: ignore[arg-type]
                status=candidate.page.status,
                content_type=candidate.page.content_type,
            ),
            metadata=candidate.metadata,
            root=candidate.root,
            notices=tuple(notices),
            attempts=selected_attempts,
        )

    def extract(
        self,
        source: str | Html,
        *,
        config: ExtractionConfig | None = None,
    ) -> Document:
        active = config or self.config
        if isinstance(source, Html):
            if active.load_mode is LoadMode.BROWSER:
                raise InputError(
                    "Browser mode requires a URL. For supplied HTML, execute the page externally and pass its final DOM to Html(...)."
                )
            content = _decode_html(source.content, source.encoding)
            page = LoadedPage(
                requested_url=source.base_url or "",
                final_url=source.base_url or "",
                html=content,
                mode="provided",
                status=None,
                content_type="text/html",
                elapsed_ms=0,
                headers={},
            )
            candidate = _extract_candidate(page, active)
            return self._finalize(
                candidate,
                source_kind="html",
                requested_url=None,
                base_url=source.base_url,
                attempts=[_attempt(candidate)],
                notices=[],
            )

        if not isinstance(source, str):
            raise InputError("Source must be an absolute URL string or getw.Html.")

        if active.load_mode is LoadMode.BROWSER:
            page = self._browser().load(source, active)
            candidate = _extract_candidate(page, active)
            return self._finalize(
                candidate,
                source_kind="url",
                requested_url=source,
                base_url=None,
                attempts=[_attempt(candidate, "explicit-browser")],
                notices=[],
            )

        static_page = self.http_loader.load(source, active)
        static_candidate: _Candidate | None = None
        static_error: GetwError | None = None
        try:
            static_candidate = _extract_candidate(static_page, active)
        except GetwError as exc:
            static_error = exc

        if active.load_mode is LoadMode.STATIC:
            if static_candidate is None:
                raise static_error or ContentNotFoundError("No static content found.")
            return self._finalize(
                static_candidate,
                source_kind="url",
                requested_url=source,
                base_url=None,
                attempts=[_attempt(static_candidate, "explicit-static")],
                notices=[],
            )

        reason = rendering_reason(
            static_page.html,
            extracted_chars=(
                static_candidate.extracted_chars if static_candidate else 0
            ),
            min_content_chars=active.min_content_chars,
            wait_for=active.wait_for,
        )
        if reason is None and static_candidate is not None:
            return self._finalize(
                static_candidate,
                source_kind="url",
                requested_url=source,
                base_url=None,
                attempts=[_attempt(static_candidate)],
                notices=[],
            )

        attempts: list[LoadAttempt] = []
        if static_candidate is not None:
            attempts.append(_attempt(static_candidate, reason))
        else:
            attempts.append(_failed_attempt(static_page, reason or "empty-extraction"))
        notices: list[Notice] = []
        try:
            browser_page = self._browser().load(source, active)
            browser_candidate = _extract_candidate(browser_page, active)
        except (CapabilityError, LoadError, ContentNotFoundError) as exc:
            if static_candidate is None or _is_obvious_shell(
                static_candidate, reason or "empty-extraction"
            ):
                raise exc from static_error
            notices.append(
                Notice(
                    code="browser_fallback_failed",
                    severity="warning",
                    message=(
                        f"Static content was retained after DOM rendering failed: {exc}"
                    ),
                )
            )
            return self._finalize(
                static_candidate,
                source_kind="url",
                requested_url=source,
                base_url=None,
                attempts=attempts,
                notices=notices,
            )

        attempts.append(_attempt(browser_candidate, reason))
        choose_browser = (
            reason == "explicit-selector"
            or static_candidate is None
            or browser_candidate.extracted_chars
            > static_candidate.extracted_chars * 1.05
            or browser_candidate.quality > static_candidate.quality + 0.03
        )
        selected = browser_candidate if choose_browser else static_candidate
        if selected is None:  # Defensive narrowing; browser_candidate exists here.
            raise ContentNotFoundError("No extraction candidate could be selected.")
        if not choose_browser:
            notices.append(
                Notice(
                    code="rendered_candidate_rejected",
                    severity="info",
                    message="The rendered DOM did not improve extraction quality; static HTML was retained.",
                )
            )
        return self._finalize(
            selected,
            source_kind="url",
            requested_url=source,
            base_url=None,
            attempts=attempts,
            notices=notices,
        )

    async def aextract(
        self,
        source: str | Html,
        *,
        config: ExtractionConfig | None = None,
    ) -> Document:
        return await asyncio.to_thread(self.extract, source, config=config)


def extract(
    source: str | Html,
    *,
    render: LoadMode | str = LoadMode.AUTO,
    wait_for: str | None = None,
    timeout: float = 30.0,
    browser_channel: str | None = None,
) -> Document:
    config = ExtractionConfig(
        load_mode=render,
        wait_for=wait_for,
        timeout=timeout,
        browser_channel=browser_channel,
    )
    return Extractor(config).extract(source)


async def aextract(
    source: str | Html,
    *,
    render: LoadMode | str = LoadMode.AUTO,
    wait_for: str | None = None,
    timeout: float = 30.0,
    browser_channel: str | None = None,
) -> Document:
    return await asyncio.to_thread(
        extract,
        source,
        render=render,
        wait_for=wait_for,
        timeout=timeout,
        browser_channel=browser_channel,
    )
