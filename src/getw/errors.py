"""Public error hierarchy for getw."""

from __future__ import annotations


class GetwError(Exception):
    """Base class for expected extraction failures."""


class InputError(GetwError):
    """The supplied source is unsupported or malformed."""


class LoadError(GetwError):
    """A web page could not be loaded."""


class ContentNotFoundError(GetwError):
    """Loading succeeded but no meaningful content could be extracted."""


class CapabilityError(GetwError):
    """An explicitly required optional capability is unavailable."""

    def __init__(self, capability: str, message: str, install_hint: str) -> None:
        self.capability = capability
        self.install_hint = install_hint
        super().__init__(f"{message} Install with: {install_hint}")


class ChallengePageError(ContentNotFoundError):
    """The selected response is an anti-bot interstitial, not page content."""

    def __init__(self, vendor: str, title: str, status: int | None) -> None:
        self.vendor = vendor
        self.title = title
        self.status = status
        super().__init__(
            f"Bot challenge detected ({vendor}): title={title!r}, status={status}."
        )
