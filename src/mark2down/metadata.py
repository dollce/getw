"""Build a rich YAML frontmatter block from the page + JSON-LD + meta tags."""

from __future__ import annotations

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
    parsed = urlparse(final_url or url)
    jsonld_data = _from_jsonld(json_ld)

    # Basic text stats for training-data triage.
    stripped = re.sub(r"\s+", " ", re.sub(r"[#*_`>\-\[\]()]", " ", markdown)).strip()
    word_count = len(stripped.split())
    char_count = len(markdown)
    # Reading time assuming ~220 wpm (mixed EN/KO gives ~180-250 in practice).
    reading_time_min = max(1, round(word_count / 220))

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
        "word_count": word_count,
        "char_count": char_count,
        "reading_time_min": reading_time_min,
        "generator": "mark2down",
    }

    # Drop empty values so the frontmatter stays lean.
    fm = {k: v for k, v in fm.items() if v not in (None, "", [], {})}

    body = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return f"---\n{body}---\n\n"


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
    """Build frontmatter for non-browser inputs such as local files or stdin."""
    stripped = re.sub(r"\s+", " ", re.sub(r"[#*_`>\-\[\]()]", " ", markdown)).strip()
    word_count = len(stripped.split())
    char_count = len(markdown)
    reading_time_min = max(1, round(word_count / 220))

    fm: dict[str, Any] = {
        "title": title.strip() or None,
        "source": source,
        "source_type": source_type,
        "path": path,
        "mime_type": mime_type,
        "extension": extension,
        "charset": charset,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "word_count": word_count,
        "char_count": char_count,
        "reading_time_min": reading_time_min,
        "generator": "mark2down",
    }
    fm = {k: v for k, v in fm.items() if v not in (None, "", [], {})}

    body = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return f"---\n{body}---\n\n"
