class SnuetlError(Exception):
    """Base application error."""


class AuthenticationRequired(SnuetlError):
    """The saved browser profile is not authenticated."""


class DiscoveryError(SnuetlError):
    """Course or file discovery failed."""


class AdapterUnavailable(DiscoveryError):
    """A discovery backend is unavailable and a fallback may be attempted."""


class ProfileInUse(SnuetlError):
    """Another process has locked the browser profile."""


class OperationCancelled(SnuetlError):
    """A cooperative long-running operation was cancelled."""
