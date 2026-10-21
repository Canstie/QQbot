"""Shared AI response errors for request parsing and structured workflows."""


class DSAPIError(RuntimeError):
    pass


class DSAPITimeoutError(DSAPIError):
    """Connection, upload or response reading exceeded the configured timeout."""


class DSAPIContentBlockedError(DSAPIError):
    """The provider explicitly declined the input or generated content."""


class DSAPIResponseError(DSAPIError):
    """The provider returned an unusable response, rather than a network failure."""


class DSAPIEmptyResponseError(DSAPIResponseError):
    """Generation ended without any final answer text."""


class DSAPIProtocolError(DSAPIResponseError):
    """The model emitted an invalid tool call or an unexpected tool invocation."""


class DSAPILengthError(DSAPIError):
    """The model stopped before completing its answer."""


class DSAPIImageError(DSAPIError):
    """A quoted image could not be loaded for the model."""


class DSAPIVisionUnsupportedError(DSAPIError):
    """The selected model has no enabled image input capability."""
