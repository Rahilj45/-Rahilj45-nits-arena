"""Codeforces API wrapper with aiohttp, retry logic, and rate limiting."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import aiohttp

from utils.exceptions import (
    CodeforcesAPIError,
    CodeforcesRateLimitError,
    CodeforcesUnavailableError,
    HandleNotFoundError,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_CF_BASE_URL = "https://codeforces.com/api"
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0  # seconds; exponential: 2, 4, 8
_RATE_LIMIT_CALLS = 5
_RATE_LIMIT_PERIOD = 1.0  # 5 calls per second (CF public API limit)


class RateLimiter:
    """Simple token-bucket-style rate limiter for async code."""

    def __init__(self, max_calls: int, period: float) -> None:
        self._max_calls = max_calls
        self._period = period
        self._calls: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Drop timestamps outside the window
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max_calls:
                sleep_for = self._period - (now - self._calls[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._calls.append(time.monotonic())


_rate_limiter = RateLimiter(_RATE_LIMIT_CALLS, _RATE_LIMIT_PERIOD)


class CodeforcesClient:
    """Async Codeforces API client.

    Usage::

        async with CodeforcesClient() as cf:
            info = await cf.get_user_info("tourist")
    """

    def __init__(self, session: Optional[aiohttp.ClientSession] = None) -> None:
        self._session = session
        self._owned = session is None

    async def __aenter__(self) -> "CodeforcesClient":
        if self._owned:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owned and self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, endpoint: str, params: Dict[str, Any]) -> Any:
        """Issue a GET request with retry logic and rate limiting.

        Args:
            endpoint: CF API method name (e.g. ``"user.info"``).
            params: Query parameters for the request.

        Returns:
            The ``result`` field from the CF API JSON response.

        Raises:
            CodeforcesRateLimitError: If CF returns HTTP 429.
            CodeforcesUnavailableError: On repeated server errors.
            CodeforcesAPIError: On any other API-level error.
        """
        url = f"{_CF_BASE_URL}/{endpoint}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            await _rate_limiter.acquire()
            try:
                assert self._session is not None, "Session not initialised"
                async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 429:
                        raise CodeforcesRateLimitError("Codeforces rate limit hit.")
                    if resp.status >= 500:
                        raise CodeforcesUnavailableError(
                            f"Codeforces server error: HTTP {resp.status}"
                        )
                    resp.raise_for_status()
                    data: Dict[str, Any] = await resp.json()
                    if data.get("status") != "OK":
                        comment = data.get("comment", "Unknown error")
                        raise CodeforcesAPIError(f"CF API error: {comment}")
                    return data["result"]
            except (CodeforcesRateLimitError, CodeforcesUnavailableError) as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "CF API attempt %d/%d failed (%s). Retrying in %.1fs.",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            except aiohttp.ClientError as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "Network error on attempt %d/%d: %s. Retrying in %.1fs.",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)

        raise CodeforcesUnavailableError(
            f"Codeforces API unavailable after {_MAX_RETRIES} retries."
        ) from last_exc

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_user_info(self, handle: str) -> Dict[str, Any]:
        """Fetch profile info for a single Codeforces user.

        Args:
            handle: Codeforces username.

        Returns:
            A dict containing user profile fields (``handle``, ``rating``,
            ``organization``, etc.).

        Raises:
            HandleNotFoundError: If the handle does not exist.
            CodeforcesAPIError: On other API errors.
        """
        try:
            result: List[Dict[str, Any]] = await self._get(
                "user.info", {"handles": handle}
            )
        except CodeforcesAPIError as exc:
            if "not found" in str(exc).lower() or "illegal" in str(exc).lower():
                raise HandleNotFoundError(f"Handle '{handle}' not found on Codeforces.") from exc
            raise
        return result[0]

    async def get_user_submissions(
        self, handle: str, count: int = 100
    ) -> List[Dict[str, Any]]:
        """Return the most recent submissions for a user.

        Args:
            handle: Codeforces username.
            count: Maximum number of submissions to return (default 100).

        Returns:
            List of submission dicts.
        """
        return await self._get(
            "user.status", {"handle": handle, "from": 1, "count": count}
        )

    async def get_problems(
        self,
        min_rating: int = 800,
        max_rating: int = 1600,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return problems within the given rating range.

        Args:
            min_rating: Minimum problem difficulty rating (inclusive).
            max_rating: Maximum problem difficulty rating (inclusive).
            tags: Optional list of CF problem tags to filter by.

        Returns:
            List of problem dicts with ``contestId``, ``index``, ``name``,
            and ``rating`` keys.
        """
        params: Dict[str, Any] = {}
        if tags:
            params["tags"] = ";".join(tags)
        result: Dict[str, Any] = await self._get("problemset.problems", params)
        problems: List[Dict[str, Any]] = result.get("problems", [])
        return [
            p
            for p in problems
            if p.get("rating") and min_rating <= p["rating"] <= max_rating
        ]

    async def get_solved_problems(self, handle: str) -> List[str]:
        """Return a list of problem IDs already solved by the user.

        Problem IDs are in the format ``"<contestId><index>"``
        (e.g. ``"1234A"``).

        Args:
            handle: Codeforces username.

        Returns:
            List of solved problem ID strings.
        """
        submissions: List[Dict[str, Any]] = await self._get(
            "user.status", {"handle": handle, "from": 1, "count": 10000}
        )
        solved: List[str] = []
        for sub in submissions:
            if sub.get("verdict") == "OK":
                prob = sub.get("problem", {})
                contest_id = prob.get("contestId", "")
                index = prob.get("index", "")
                if contest_id and index:
                    solved.append(f"{contest_id}{index}")
        return solved

    async def verify_uuid_in_org(self, handle: str, uuid: str) -> bool:
        """Check whether the user's Codeforces Organisation field contains *uuid*.

        Args:
            handle: Codeforces username.
            uuid: The UUID that should appear in the Organisation field.

        Returns:
            ``True`` if the UUID is present, ``False`` otherwise.
        """
        info = await self.get_user_info(handle)
        org: str = info.get("organization", "") or ""
        return uuid in org
