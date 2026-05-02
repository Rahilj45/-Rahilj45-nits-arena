"""Mock Codeforces API tests for utils/cf_api.py."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from utils.cf_api import CodeforcesClient
from utils.exceptions import (
    CodeforcesAPIError,
    CodeforcesUnavailableError,
    HandleNotFoundError,
)

CF_BASE = "https://codeforces.com/api"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_aioresponses():
    """Context manager that patches aiohttp requests."""
    with aioresponses() as m:
        yield m


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_info_success(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/user.info?handles=tourist",
        payload={
            "status": "OK",
            "result": [{"handle": "tourist", "rating": 3800, "organization": ""}],
        },
    )
    async with CodeforcesClient() as cf:
        info = await cf.get_user_info("tourist")
    assert info["handle"] == "tourist"
    assert info["rating"] == 3800


@pytest.mark.asyncio
async def test_get_user_info_handle_not_found(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/user.info?handles=nonexistent_user_xyz",
        payload={"status": "FAILED", "comment": "handles: User with handle nonexistent_user_xyz not found"},
    )
    async with CodeforcesClient() as cf:
        with pytest.raises(HandleNotFoundError):
            await cf.get_user_info("nonexistent_user_xyz")


@pytest.mark.asyncio
async def test_get_user_info_api_error(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/user.info?handles=baduser",
        payload={"status": "FAILED", "comment": "Some other error"},
    )
    async with CodeforcesClient() as cf:
        with pytest.raises(CodeforcesAPIError):
            await cf.get_user_info("baduser")


# ---------------------------------------------------------------------------
# get_problems
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_problems_filters_by_rating(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/problemset.problems",
        payload={
            "status": "OK",
            "result": {
                "problems": [
                    {"contestId": 1, "index": "A", "name": "Easy", "rating": 800},
                    {"contestId": 2, "index": "B", "name": "Medium", "rating": 1200},
                    {"contestId": 3, "index": "C", "name": "Hard", "rating": 2000},
                ],
                "problemStatistics": [],
            },
        },
    )
    async with CodeforcesClient() as cf:
        problems = await cf.get_problems(min_rating=800, max_rating=1200)
    assert len(problems) == 2
    assert all(800 <= p["rating"] <= 1200 for p in problems)


# ---------------------------------------------------------------------------
# get_solved_problems
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_solved_problems_returns_accepted(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/user.status?handle=tourist&from=1&count=10000",
        payload={
            "status": "OK",
            "result": [
                {"verdict": "OK", "problem": {"contestId": 1, "index": "A"}},
                {"verdict": "WRONG_ANSWER", "problem": {"contestId": 2, "index": "B"}},
                {"verdict": "OK", "problem": {"contestId": 3, "index": "C"}},
            ],
        },
    )
    async with CodeforcesClient() as cf:
        solved = await cf.get_solved_problems("tourist")
    assert "1A" in solved
    assert "3C" in solved
    assert "2B" not in solved


# ---------------------------------------------------------------------------
# verify_uuid_in_org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uuid_in_org_success(mock_aioresponses) -> None:
    uuid_token = "test-uuid-1234"
    mock_aioresponses.get(
        f"{CF_BASE}/user.info?handles=tourist",
        payload={
            "status": "OK",
            "result": [{"handle": "tourist", "rating": 3800, "organization": uuid_token}],
        },
    )
    async with CodeforcesClient() as cf:
        result = await cf.verify_uuid_in_org("tourist", uuid_token)
    assert result is True


@pytest.mark.asyncio
async def test_verify_uuid_in_org_missing(mock_aioresponses) -> None:
    mock_aioresponses.get(
        f"{CF_BASE}/user.info?handles=tourist",
        payload={
            "status": "OK",
            "result": [{"handle": "tourist", "rating": 3800, "organization": "SomeOtherOrg"}],
        },
    )
    async with CodeforcesClient() as cf:
        result = await cf.verify_uuid_in_org("tourist", "expected-uuid")
    assert result is False


# ---------------------------------------------------------------------------
# Retry on server error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_server_error(mock_aioresponses) -> None:
    """Client should retry on 500 errors and raise after max retries."""
    for _ in range(3):  # 3 retries
        mock_aioresponses.get(
            f"{CF_BASE}/user.info?handles=tourist",
            status=500,
        )
    async with CodeforcesClient() as cf:
        with pytest.raises(CodeforcesUnavailableError):
            await cf.get_user_info("tourist")
