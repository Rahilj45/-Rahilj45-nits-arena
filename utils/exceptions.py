"""Custom exception hierarchy for NITS Arena."""


class NitsArenaError(Exception):
    """Base exception for all NITS Arena errors."""

    pass


# ---------------------------------------------------------------------------
# Verification exceptions
# ---------------------------------------------------------------------------


class VerificationError(NitsArenaError):
    """Raised when account verification fails."""

    pass


class AlreadyVerifiedError(VerificationError):
    """Raised when a user attempts to verify an already-verified account."""

    pass


class UUIDMismatchError(VerificationError):
    """Raised when the UUID in the Codeforces profile does not match the expected UUID."""

    pass


class HandleNotFoundError(VerificationError):
    """Raised when the given Codeforces handle does not exist."""

    pass


# ---------------------------------------------------------------------------
# Match / Lockout exceptions
# ---------------------------------------------------------------------------


class MatchError(NitsArenaError):
    """Base exception for match-related errors."""

    pass


class MatchNotFoundError(MatchError):
    """Raised when a referenced match does not exist."""

    pass


class MatchAlreadyActiveError(MatchError):
    """Raised when a user tries to start a match while one is already active."""

    pass


class ProblemLockedError(MatchError):
    """Raised when a user tries to claim a problem that is already locked."""

    pass


class InvalidSubmissionError(MatchError):
    """Raised when a submission does not satisfy match constraints."""

    pass


# ---------------------------------------------------------------------------
# Codeforces API exceptions
# ---------------------------------------------------------------------------


class CodeforcesAPIError(NitsArenaError):
    """Base exception for Codeforces API errors."""

    pass


class CodeforcesRateLimitError(CodeforcesAPIError):
    """Raised when the Codeforces API rate limit is exceeded."""

    pass


class CodeforcesUnavailableError(CodeforcesAPIError):
    """Raised when the Codeforces API is temporarily unavailable."""

    pass


# ---------------------------------------------------------------------------
# Database exceptions
# ---------------------------------------------------------------------------


class DatabaseError(NitsArenaError):
    """Base exception for database errors."""

    pass


class UserNotFoundError(DatabaseError):
    """Raised when a referenced user does not exist in the database."""

    pass
