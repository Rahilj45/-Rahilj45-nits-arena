"""Custom exception hierarchy for NITS Arena."""


class NitsArenaError(Exception):
    """Base exception for all NITS Arena errors."""


# ---------------------------------------------------------------------------
# Verification exceptions
# ---------------------------------------------------------------------------


class VerificationError(NitsArenaError):
    """Raised when account verification fails."""


class AlreadyVerifiedError(VerificationError):
    """Raised when a user attempts to verify an already-verified account."""


class UUIDMismatchError(VerificationError):
    """Raised when the UUID in the Codeforces profile does not match the expected UUID."""


class HandleNotFoundError(VerificationError):
    """Raised when the given Codeforces handle does not exist."""


# ---------------------------------------------------------------------------
# Match / Lockout exceptions
# ---------------------------------------------------------------------------


class MatchError(NitsArenaError):
    """Base exception for match-related errors."""


class MatchNotFoundError(MatchError):
    """Raised when a referenced match does not exist."""


class MatchAlreadyActiveError(MatchError):
    """Raised when a user tries to start a match while one is already active."""


class ProblemLockedError(MatchError):
    """Raised when a user tries to claim a problem that is already locked."""


class InvalidSubmissionError(MatchError):
    """Raised when a submission does not satisfy match constraints."""


# ---------------------------------------------------------------------------
# Codeforces API exceptions
# ---------------------------------------------------------------------------


class CodeforcesAPIError(NitsArenaError):
    """Base exception for Codeforces API errors."""


class CodeforcesRateLimitError(CodeforcesAPIError):
    """Raised when the Codeforces API rate limit is exceeded."""


class CodeforcesUnavailableError(CodeforcesAPIError):
    """Raised when the Codeforces API is temporarily unavailable."""


# ---------------------------------------------------------------------------
# Database exceptions
# ---------------------------------------------------------------------------


class DatabaseError(NitsArenaError):
    """Base exception for database errors."""


class UserNotFoundError(DatabaseError):
    """Raised when a referenced user does not exist in the database."""
