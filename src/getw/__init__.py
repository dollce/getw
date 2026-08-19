"""Source-grounded web extraction for LLM inputs."""

from .errors import (
    CapabilityError,
    ContentNotFoundError,
    GetwError,
    InputError,
    LoadError,
)
from .extraction import Extractor, aextract, extract
from .models import (
    Annotation,
    Document,
    ExtractionConfig,
    Html,
    LoadMode,
    Metadata,
    Node,
    Notice,
    SourceInfo,
    TextSpan,
)
from .semantics import (
    SemanticExample,
    SemanticExtraction,
    SemanticTask,
    enrich,
)

__version__ = "2.0.0"

__all__ = [
    "Annotation",
    "CapabilityError",
    "ContentNotFoundError",
    "Document",
    "ExtractionConfig",
    "Extractor",
    "GetwError",
    "Html",
    "InputError",
    "LoadError",
    "LoadMode",
    "Metadata",
    "Node",
    "Notice",
    "SemanticExample",
    "SemanticExtraction",
    "SemanticTask",
    "SourceInfo",
    "TextSpan",
    "__version__",
    "aextract",
    "enrich",
    "extract",
]
