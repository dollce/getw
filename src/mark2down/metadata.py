"""Build a rich YAML frontmatter block from the page + JSON-LD + meta tags."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import yaml
from dateutil import parser as date_parser


def _pick(meta: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        val = meta.get(key) or meta.get(key.lower())
        if val:
            val = val.strip()
            if val:
                return val
    return None


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return value.strip()


def _keywords(meta: dict[str, str]) -> list[str]:
    raw = _pick(meta, "keywords", "article:tag", "news_keywords") or ""
    if not raw:
        return []
    parts = re.split(r"[,;|]", raw)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        k = p.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _from_jsonld(json_ld: list[Any]) -> dict[str, Any]:
    """Pull structured data (Article / NewsArticle / BlogPosting) from JSON-LD blocks."""
    result: dict[str, Any] = {}
    wanted = {
        "article",
        "newsarticle",
        "blogposting",
        "techarticle",
        "scholarlyarticle",
        "webpage",
    }

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
            return
        if not isinstance(obj, dict):
            return
        t = obj.get("@type")
        if isinstance(t, list):
            type_strs = [str(x).lower() for x in t]
        else:
            type_strs = [str(t).lower()] if t else []
        if any(ts in wanted for ts in type_strs):
            if "headline" in obj and "title" not in result:
                result["title"] = obj["headline"]
            if "description" in obj and "description" not in result:
                result["description"] = obj["description"]
            author = obj.get("author")
            if author and "author" not in result:
                if isinstance(author, list):
                    result["author"] = [a.get("name") if isinstance(a, dict) else str(a) for a in author if a]
                elif isinstance(author, dict):
                    if author.get("name"):
                        result["author"] = author["name"]
                else:
                    result["author"] = str(author)
            if obj.get("datePublished") and "published_at" not in result:
                result["published_at"] = obj["datePublished"]
            if obj.get("dateModified") and "modified_at" not in result:
                result["modified_at"] = obj["dateModified"]
            if obj.get("inLanguage") and "language" not in result:
                lang = obj["inLanguage"]
                result["language"] = lang if isinstance(lang, str) else lang.get("name")
            if obj.get("keywords") and "keywords" not in result:
                kw = obj["keywords"]
                if isinstance(kw, list):
                    result["keywords"] = [str(k) for k in kw]
                else:
                    result["keywords"] = [k.strip() for k in re.split(r"[,;|]", str(kw)) if k.strip()]
            publisher = obj.get("publisher")
            if isinstance(publisher, dict) and publisher.get("name") and "publisher" not in result:
                result["publisher"] = publisher["name"]
        # Recurse into nested values.
        for v in obj.values():
            walk(v)

    walk(json_ld)
    return result


def _text_stats(markdown: str) -> dict[str, int]:
    stripped = re.sub(r"\s+", " ", re.sub(r"[#*_`>\-\[\]()]", " ", markdown)).strip()
    word_count = len(stripped.split())
    char_count = len(markdown)
    return {
        "word_count": word_count,
        "char_count": char_count,
        "reading_time_min": max(1, round(word_count / 220)),
    }


def build_page_metadata(
    *,
    url: str,
    final_url: str,
    title: str,
    html_lang: str,
    canonical: str | None,
    meta: dict[str, str],
    json_ld: list[Any],
    markdown: str,
) -> dict[str, Any]:
    parsed = urlparse(final_url or url)
    jsonld_data = _from_jsonld(json_ld)

    resolved_title = (
        title
        or jsonld_data.get("title")
        or _pick(meta, "og:title", "twitter:title", "title")
        or ""
    )

    description = (
        jsonld_data.get("description")
        or _pick(meta, "description", "og:description", "twitter:description")
    )

    author = jsonld_data.get("author") or _pick(meta, "author", "article:author", "dc.creator")
    publisher = jsonld_data.get("publisher") or _pick(meta, "og:site_name", "application-name")
    language = (
        jsonld_data.get("language")
        or html_lang
        or _pick(meta, "og:locale", "content-language")
    )
    published_at = _normalize_date(
        jsonld_data.get("published_at")
        or _pick(meta, "article:published_time", "og:published_time", "date", "dc.date")
    )
    modified_at = _normalize_date(
        jsonld_data.get("modified_at")
        or _pick(meta, "article:modified_time", "og:updated_time", "last-modified")
    )

    keywords = jsonld_data.get("keywords") or _keywords(meta)

    image = _pick(meta, "og:image", "twitter:image", "image")

    fm: dict[str, Any] = {
        "title": resolved_title.strip() or None,
        "source_url": url,
        "final_url": final_url if final_url and final_url != url else None,
        "canonical_url": canonical,
        "domain": parsed.netloc or None,
        "description": (description or "").strip() or None,
        "author": author,
        "publisher": publisher,
        "language": language,
        "keywords": keywords or None,
        "image": image,
        "published_at": published_at,
        "modified_at": modified_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "generator": "mark2down",
    }
    fm.update(_text_stats(markdown))

    # Drop empty values so the frontmatter stays lean.
    return {k: v for k, v in fm.items() if v not in (None, "", [], {})}


def _dump_frontmatter(fm: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return f"---\n{body}---\n\n"


def build_frontmatter(
    *,
    url: str,
    final_url: str,
    title: str,
    html_lang: str,
    canonical: str | None,
    meta: dict[str, str],
    json_ld: list[Any],
    markdown: str,
) -> str:
    return _dump_frontmatter(
        build_page_metadata(
            url=url,
            final_url=final_url,
            title=title,
            html_lang=html_lang,
            canonical=canonical,
            meta=meta,
            json_ld=json_ld,
            markdown=markdown,
        )
    )


def build_source_metadata(
    *,
    source: str,
    source_type: str,
    title: str,
    markdown: str,
    path: str | None = None,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
) -> dict[str, Any]:
    """Build frontmatter for non-browser inputs such as local files or stdin."""
    fm: dict[str, Any] = {
        "title": title.strip() or None,
        "source": source,
        "source_type": source_type,
        "path": path,
        "mime_type": mime_type,
        "extension": extension,
        "charset": charset,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "generator": "mark2down",
    }
    fm.update(_text_stats(markdown))
    return {k: v for k, v in fm.items() if v not in (None, "", [], {})}


def build_source_frontmatter(
    *,
    source: str,
    source_type: str,
    title: str,
    markdown: str,
    path: str | None = None,
    mime_type: str | None = None,
    extension: str | None = None,
    charset: str | None = None,
) -> str:
    return _dump_frontmatter(
        build_source_metadata(
            source=source,
            source_type=source_type,
            title=title,
            markdown=markdown,
            path=path,
            mime_type=mime_type,
            extension=extension,
            charset=charset,
        )
    )


def _html_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _html_text(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return html.escape(str(value), quote=False)


def _metadata_list_items(metadata: dict[str, Any]) -> str:
    rows: list[str] = []
    for key, value in metadata.items():
        if value in (None, "", [], {}):
            continue
        label = key.replace("_", " ").title()
        rows.append(
            f"<dt>{_html_text(label)}</dt><dd>{_html_text(value)}</dd>"
        )
    return "\n".join(rows)


def _safe_json_script(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return text.replace("</", "<\\/")


def build_html_document(
    *,
    metadata: dict[str, Any],
    content_html: str,
    raw_meta: dict[str, str] | None = None,
    raw_json_ld: list[Any] | None = None,
) -> str:
    """Build an LLM-oriented HTML document around cleaned article HTML."""
    title = str(metadata.get("title") or metadata.get("source") or "Untitled")
    language = str(metadata.get("language") or "und")
    canonical = metadata.get("canonical_url")
    source_url = metadata.get("source_url") or metadata.get("final_url") or metadata.get("source")
    description = metadata.get("description")

    head_lines = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="generator" content="mark2down">',
    ]
    if description:
        head_lines.append(f'<meta name="description" content="{_html_attr(description)}">')
    if source_url:
        head_lines.append(f'<meta name="source" content="{_html_attr(source_url)}">')
    if canonical:
        head_lines.append(f'<link rel="canonical" href="{_html_attr(canonical)}">')

    payload = {
        "metadata": metadata,
        "raw_meta": raw_meta or {},
        "json_ld": raw_json_ld or [],
    }
    json_payload = _safe_json_script(payload)
    metadata_items = _metadata_list_items(metadata)

    return (
        "<!doctype html>\n"
        f'<html lang="{_html_attr(language)}">\n'
        "<head>\n"
        + "\n".join(head_lines)
        + "\n"
        f"<title>{_html_text(title)}</title>\n"
        '<script type="application/json" id="mark2down-metadata-json">\n'
        f"{json_payload}\n"
        "</script>\n"
        "</head>\n"
        "<body>\n"
        '<section id="mark2down-metadata" aria-label="Document metadata">\n'
        "<h1>Document Metadata</h1>\n"
        "<dl>\n"
        f"{metadata_items}\n"
        "</dl>\n"
        "</section>\n"
        '<main id="mark2down-content">\n'
        f"{content_html.strip()}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
