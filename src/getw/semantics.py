"""Optional, source-grounded semantic enrichment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import CapabilityError, InputError
from .models import Annotation, Document, JsonValue, TextSpan


@dataclass(frozen=True, slots=True)
class SemanticExtraction:
    label: str
    text: str
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SemanticExample:
    text: str
    extractions: tuple[SemanticExtraction, ...]


@dataclass(frozen=True, slots=True)
class SemanticTask:
    """Provider-neutral description of a source-grounded extraction task."""

    instruction: str
    model_id: str | None = None
    examples: tuple[SemanticExample, ...] = ()
    output_schema: Mapping[str, Any] | None = None
    model: Any = field(default=None, repr=False, compare=False)
    grounded_only: bool = True
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("SemanticTask.instruction cannot be empty")
        if not self.examples and self.output_schema is None:
            raise ValueError("SemanticTask requires examples or output_schema")
        if self.model is None and not self.model_id:
            raise ValueError("SemanticTask requires model_id or model")


def _targets_for_interval(
    document: Document,
    start: int,
    end: int,
) -> tuple[TextSpan, ...]:
    targets: list[TextSpan] = []
    for span in document.grounding_text().spans:
        overlap_start = max(start, span.start)
        overlap_end = min(end, span.end)
        if overlap_start >= overlap_end:
            continue
        targets.append(
            TextSpan(
                node_id=span.node_id,
                start=overlap_start - span.start,
                end=overlap_end - span.start,
            )
        )
    return tuple(targets)


def enrich(document: Document, task: SemanticTask) -> Document:
    """Add semantic annotations without mutating the structural IR."""

    try:
        import langextract as lx  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise CapabilityError(
            "semantic_enrichment",
            "Semantic enrichment was requested but its backend is not installed.",
            "pip install 'getw[semantic]'",
        ) from exc

    examples = [
        lx.data.ExampleData(
            text=example.text,
            extractions=[
                lx.data.Extraction(
                    extraction_class=item.label,
                    extraction_text=item.text,
                    attributes=dict(item.attributes) or None,
                )
                for item in example.extractions
            ],
        )
        for example in task.examples
    ]
    kwargs: dict[str, Any] = {
        "text_or_documents": document.grounding_text().text,
        "prompt_description": task.instruction,
        **dict(task.options),
    }
    if examples:
        kwargs["examples"] = examples
    if task.output_schema is not None:
        kwargs["output_schema"] = dict(task.output_schema)
    if task.model is not None:
        kwargs["model"] = task.model
    elif task.model_id:
        kwargs["model_id"] = task.model_id

    result = lx.extract(**kwargs)
    extractions = getattr(result, "extractions", None) or ()
    annotations: list[Annotation] = list(document.annotations)
    for item in extractions:
        interval = getattr(item, "char_interval", None)
        start = getattr(interval, "start_pos", None) if interval is not None else None
        end = getattr(interval, "end_pos", None) if interval is not None else None
        grounded = isinstance(start, int) and isinstance(end, int) and start < end
        if task.grounded_only and not grounded:
            continue
        if isinstance(start, int) and isinstance(end, int) and start < end:
            targets = _targets_for_interval(document, start, end)
        else:
            targets = ()
        if grounded and not targets:
            raise InputError(
                "Semantic backend returned an interval outside getw's grounding text."
            )
        annotations.append(
            Annotation(
                label=str(getattr(item, "extraction_class", "unknown")),
                text=str(getattr(item, "extraction_text", "")),
                attributes=dict(getattr(item, "attributes", None) or {}),
                targets=targets,
                grounded=grounded,
            )
        )
    return document.with_annotations(tuple(annotations))
