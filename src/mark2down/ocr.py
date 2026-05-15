"""OCR helpers backed by macOS Vision through ocrmac."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OcrOptions:
    enabled: bool = False
    max_images: int = 10
    languages: tuple[str, ...] = ()
    min_confidence: float = 0.0


def format_ocr_block(text: str, label: str | None = None) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not cleaned:
        return ""
    cleaned = cleaned.replace("```", "'''")
    heading = f"**Image OCR: {label}**" if label else "**Image OCR**"
    return f"{heading}\n\n```text\n{cleaned}\n```"


def extract_text_from_image(
    image_bytes: bytes,
    *,
    languages: tuple[str, ...] = (),
    min_confidence: float = 0.0,
) -> str:
    """Run OCR over image bytes and return recognized text in reading order."""
    try:
        from PIL import Image, UnidentifiedImageError
        from ocrmac import ocrmac
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("OCR requires the ocrmac and Pillow packages on macOS.") from exc

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (OSError, UnidentifiedImageError):
        return ""

    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")

    try:
        raw_results = ocrmac.OCR(
            image,
            recognition_level="accurate",
            language_preference=list(languages) or None,
            confidence_threshold=min_confidence,
            detail=True,
        ).recognize()
    except Exception as exc:
        raise RuntimeError(f"OCR failed: {exc}") from exc

    pieces: list[str] = []
    for item in raw_results:
        text = _ocr_item_text(item)
        confidence = _ocr_item_confidence(item)
        if text and confidence >= min_confidence:
            pieces.append(text)

    return "\n".join(pieces).strip()


def _ocr_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, (tuple, list)) and item:
        return str(item[0]).strip()
    return ""


def _ocr_item_confidence(item: Any) -> float:
    if isinstance(item, (tuple, list)) and len(item) > 1:
        try:
            return float(item[1])
        except (TypeError, ValueError):
            return 0.0
    return 1.0


class OcrCollector:
    """Bounded OCR runner for one conversion."""

    def __init__(self, options: OcrOptions | None) -> None:
        self.options = options or OcrOptions()
        self.attempts = 0
        self.failed = False

    @property
    def enabled(self) -> bool:
        return self.options.enabled and self.options.max_images > 0 and not self.failed

    def run(self, image_bytes: bytes, *, label: str | None = None) -> str:
        if not self.enabled or self.attempts >= self.options.max_images:
            return ""
        self.attempts += 1
        try:
            text = extract_text_from_image(
                image_bytes,
                languages=self.options.languages,
                min_confidence=self.options.min_confidence,
            )
        except Exception:
            self.failed = True
            return ""
        return format_ocr_block(text, label)
