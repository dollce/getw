"""Stable, provider-neutral document model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal, TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class LoadMode(str, Enum):
    """How URL input is loaded before extraction."""

    AUTO = "auto"
    STATIC = "static"
    BROWSER = "browser"


class ExtractionMode(str, Enum):
    """Trafilatura precision/recall preference."""

    BALANCED = "balanced"
    PRECISION = "precision"
    RECALL = "recall"


@dataclass(frozen=True, slots=True)
class Html:
    """Explicit HTML input; plain strings are always interpreted as URLs."""

    content: str | bytes
    base_url: str | None = None
    encoding: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    """Advanced extraction controls with conservative defaults."""

    load_mode: LoadMode | str = LoadMode.AUTO
    extraction_mode: ExtractionMode | str = ExtractionMode.BALANCED
    timeout: float = 30.0
    settle_timeout: float = 8.0
    stable_for: float = 0.8
    wait_for: str | None = None
    browser_channel: str | None = None
    headless: bool = True
    min_content_chars: int = 160
    max_response_bytes: int = 10_000_000
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 getw/2"
    )
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.load_mode, str):
            object.__setattr__(self, "load_mode", LoadMode(self.load_mode))
        if isinstance(self.extraction_mode, str):
            object.__setattr__(
                self, "extraction_mode", ExtractionMode(self.extraction_mode)
            )
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if self.settle_timeout < 0 or self.stable_for < 0:
            raise ValueError("settle_timeout and stable_for cannot be negative")
        if self.min_content_chars < 1:
            raise ValueError("min_content_chars must be positive")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")


@dataclass(frozen=True, slots=True)
class Node:
    """A node in getw's normalized semantic document tree."""

    id: str
    kind: str
    text: str | None = None
    attrs: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple[Node, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "id": self.id,
            "kind": self.kind,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.attrs:
            result["attrs"] = dict(self.attrs)
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Node:
        return cls(
            id=str(value["id"]),
            kind=str(value["kind"]),
            text=value.get("text"),
            attrs=dict(value.get("attrs") or {}),
            children=tuple(cls.from_dict(item) for item in value.get("children") or ()),
        )


@dataclass(frozen=True, slots=True)
class TextSpan:
    node_id: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Annotation:
    """A provider-neutral, source-grounded semantic extraction."""

    label: str
    text: str
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)
    targets: tuple[TextSpan, ...] = ()
    grounded: bool = True


@dataclass(frozen=True, slots=True)
class SourceInfo:
    input_kind: Literal["url", "html"]
    requested_url: str | None
    final_url: str | None
    base_url: str | None
    loaded_via: Literal["provided", "static", "browser"]
    status: int | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class Metadata:
    title: str | None = None
    description: str | None = None
    authors: tuple[str, ...] = ()
    site_name: str | None = None
    language: str | None = None
    canonical_url: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    image: str | None = None
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    extra: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Notice:
    code: str
    severity: Literal["info", "warning"]
    message: str


@dataclass(frozen=True, slots=True)
class LoadAttempt:
    mode: Literal["provided", "static", "browser"]
    url: str | None
    final_url: str | None
    status: int | None
    elapsed_ms: int
    html_chars: int
    extracted_chars: int
    quality: float
    selected: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class GroundingSpan:
    node_id: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class GroundedText:
    text: str
    spans: tuple[GroundingSpan, ...]


@dataclass(frozen=True, slots=True)
class Document:
    """Normalized extraction result and the sole public return type."""

    schema_version: Literal["1"]
    source: SourceInfo
    metadata: Metadata
    root: Node
    annotations: tuple[Annotation, ...] = ()
    notices: tuple[Notice, ...] = ()
    attempts: tuple[LoadAttempt, ...] = ()

    @property
    def text(self) -> str:
        """Compact, non-summarized representation intended for LLM input."""

        return self.render("compact")

    def render(
        self,
        format: Literal["compact", "markdown", "plain"] = "compact",
    ) -> str:
        from .rendering import render_document

        return render_document(self, format)

    def grounding_text(self) -> GroundedText:
        from .rendering import grounding_text

        return grounding_text(self.root)

    def with_annotations(self, annotations: tuple[Annotation, ...]) -> Document:
        return replace(self, annotations=annotations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": {
                "input_kind": self.source.input_kind,
                "requested_url": self.source.requested_url,
                "final_url": self.source.final_url,
                "base_url": self.source.base_url,
                "loaded_via": self.source.loaded_via,
                "status": self.source.status,
                "content_type": self.source.content_type,
            },
            "metadata": {
                "title": self.metadata.title,
                "description": self.metadata.description,
                "authors": list(self.metadata.authors),
                "site_name": self.metadata.site_name,
                "language": self.metadata.language,
                "canonical_url": self.metadata.canonical_url,
                "published_at": self.metadata.published_at,
                "modified_at": self.metadata.modified_at,
                "image": self.metadata.image,
                "categories": list(self.metadata.categories),
                "tags": list(self.metadata.tags),
                "extra": dict(self.metadata.extra),
            },
            "root": self.root.to_dict(),
            "annotations": [
                {
                    "label": item.label,
                    "text": item.text,
                    "attributes": dict(item.attributes),
                    "targets": [
                        {"node_id": span.node_id, "start": span.start, "end": span.end}
                        for span in item.targets
                    ],
                    "grounded": item.grounded,
                }
                for item in self.annotations
            ],
            "notices": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                }
                for item in self.notices
            ],
            "attempts": [
                {
                    "mode": item.mode,
                    "url": item.url,
                    "final_url": item.final_url,
                    "status": item.status,
                    "elapsed_ms": item.elapsed_ms,
                    "html_chars": item.html_chars,
                    "extracted_chars": item.extracted_chars,
                    "quality": item.quality,
                    "selected": item.selected,
                    "reason": item.reason,
                }
                for item in self.attempts
            ],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            self.to_dict(), ensure_ascii=False, indent=indent, separators=separators
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Document:
        source = value["source"]
        metadata = value["metadata"]
        return cls(
            schema_version="1",
            source=SourceInfo(**source),
            metadata=Metadata(
                **{
                    **metadata,
                    "authors": tuple(metadata.get("authors") or ()),
                    "categories": tuple(metadata.get("categories") or ()),
                    "tags": tuple(metadata.get("tags") or ()),
                }
            ),
            root=Node.from_dict(value["root"]),
            annotations=tuple(
                Annotation(
                    label=item["label"],
                    text=item["text"],
                    attributes=item.get("attributes") or {},
                    targets=tuple(
                        TextSpan(**span) for span in item.get("targets") or ()
                    ),
                    grounded=bool(item.get("grounded", True)),
                )
                for item in value.get("annotations") or ()
            ),
            notices=tuple(Notice(**item) for item in value.get("notices") or ()),
            attempts=tuple(LoadAttempt(**item) for item in value.get("attempts") or ()),
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> Document:
        return cls.from_dict(json.loads(value))
