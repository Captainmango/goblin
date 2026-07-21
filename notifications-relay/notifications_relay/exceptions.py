from __future__ import annotations

"""Custom exceptions for the notifications relay."""


class RelayError(Exception):
    """Base relay error."""


class AuthenticationError(RelayError):
    """API key authentication failed."""


class PairingNotFoundError(RelayError):
    """No active pairing was found for the supplied token."""


class PairingExpiredError(RelayError):
    """The pairing token has expired."""


class ProviderNotRegisteredError(RelayError):
    """The requested channel provider is not registered."""


class SendError(RelayError):
    """An outbound message could not be sent."""


class AskTimeoutError(RelayError):
    """No reply was received before the ask timeout elapsed."""
