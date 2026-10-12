"""Exception types shared across driftcheck modules."""


class DriftcheckError(Exception):
    """Base class for all driftcheck errors."""


class ConfigError(DriftcheckError):
    """Raised when a config file is missing, unreadable, or missing required keys."""


class SpecError(DriftcheckError):
    """Raised when an OpenAPI spec cannot be loaded, parsed, or validated."""
