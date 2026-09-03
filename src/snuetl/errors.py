class SnuetlError(Exception):
    """Base application error."""


class AuthenticationRequired(SnuetlError):
    """The saved browser profile is not authenticated."""


class DiscoveryError(SnuetlError):
    """Course or file discovery failed."""


class ProfileInUse(SnuetlError):
    """Another process has locked the browser profile."""
