class BdayblazeError(Exception):
    """Base service error."""


class ValidationError(BdayblazeError):
    """Raised when user input or config is invalid."""


class NotFoundError(BdayblazeError):
    """Raised when a requested record is missing."""
