"""Exceptions raised by outbox-mcp.

The messages are written to be shown to the AI assistant. They never contain credentials.
"""

from __future__ import annotations


class OutboxError(Exception):
    """Base class for all expected errors."""


class ConfigError(OutboxError):
    """The configuration or the recipients file is missing or invalid."""


class ValidationError(OutboxError, ValueError):
    """A subject, text or address was rejected."""


class AttachmentRejected(OutboxError, ValueError):
    """An attachment was refused. The whole draft is refused with it; nothing is prepared."""


class SourceRejected(OutboxError, ValueError):
    """A file source, a rule on it or a stored file was refused. `category` says why:
    input (malformed request), state (source missing or not active), permission (path, name,
    type or rules), integrity (hash does not match), content (size, text or secret check) or
    quota. Nothing was changed."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


class RecipientNotAllowed(OutboxError):
    """The recipient is not on the allowed recipient list."""


class RateLimitExceeded(OutboxError):
    """A sending or preparing limit has been reached."""


class DraftNotFound(OutboxError):
    """The draft does not exist (or belongs to someone else)."""


class DraftNotSendable(OutboxError):
    """The draft is not pending any more, so it cannot be sent or cancelled."""

    def __init__(self, status: str) -> None:
        self.status = status
        if status == "expired":
            text = (
                "This draft has expired. Prepare a new draft with prepare_email "
                "and ask the user to confirm again."
            )
        elif status == "unknown":
            text = (
                "This draft was already used and its result is unknown. It cannot be "
                "sent again. Tell the user, and only prepare a new draft if they ask for it."
            )
        else:
            text = (
                f"This draft is not pending (status: {status}). A draft can be sent only "
                "once and only while it is pending. Prepare a new draft if needed."
            )
        super().__init__(text)
