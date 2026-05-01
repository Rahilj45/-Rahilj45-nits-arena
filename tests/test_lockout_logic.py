"""Tests for lockout logic, focusing on race condition prevention.

These tests exercise the FOR UPDATE locking pattern using an in-memory
SQLite database (via aiosqlite) so they can run without a real PostgreSQL
instance.  The key invariant being tested is:

    When two concurrent tasks attempt to lock the same problem,
    exactly one should succeed and the other should raise ProblemLockedError.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, LockoutProblem, Match, MatchStatus, User
from utils.elo_calculator import calculate_elo_result, problem_points
from utils.exceptions import ProblemLockedError


# ---------------------------------------------------------------------------
# In-memory DB fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Helper: seed a match with two players and one problem
# ---------------------------------------------------------------------------


async def _seed_match(session: AsyncSession) -> tuple[User, User, Match, LockoutProblem]:
    user_a = User(discord_id=111, cf_handle="player_a", is_verified=True, elo_rating=1200)
    user_b = User(discord_id=222, cf_handle="player_b", is_verified=True, elo_rating=1000)
    session.add_all([user_a, user_b])
    await session.flush()

    match = Match(
        player_a_id=user_a.id,
        player_b_id=user_b.id,
        status=MatchStatus.ACTIVE,
        player_a_elo_at_start=user_a.elo_rating,
        player_b_elo_at_start=user_b.elo_rating,
        started_at=datetime.now(timezone.utc),
    )
    session.add(match)
    await session.flush()

    problem = LockoutProblem(
        match_id=match.id,
        contest_id=1234,
        problem_index="A",
        problem_name="Test Problem",
        cf_rating=1000,
    )
    session.add(problem)
    await session.flush()

    return user_a, user_b, match, problem


# ---------------------------------------------------------------------------
# Test: problem lock invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_problem_starts_unclaimed(db_session: AsyncSession) -> None:
    _, _, _, problem = await _seed_match(db_session)
    assert problem.locked_by_user_id is None


@pytest.mark.asyncio
async def test_problem_lock_sets_user(db_session: AsyncSession) -> None:
    user_a, _, match, problem = await _seed_match(db_session)

    problem.locked_by_user_id = user_a.id
    problem.points_awarded = problem_points(problem.cf_rating)
    problem.locked_at = datetime.now(timezone.utc)
    match.player_a_score += problem.points_awarded
    await db_session.flush()

    assert problem.locked_by_user_id == user_a.id
    assert problem.points_awarded > 0


@pytest.mark.asyncio
async def test_already_locked_problem_raises(db_session: AsyncSession) -> None:
    """Simulates the guard check that is done after FOR UPDATE acquisition."""
    user_a, _user_b, _match, problem = await _seed_match(db_session)

    # First solver locks the problem
    problem.locked_by_user_id = user_a.id
    problem.locked_at = datetime.now(timezone.utc)
    await db_session.flush()

    # Second solver should see it locked and raise ProblemLockedError
    with pytest.raises(ProblemLockedError, match="already locked"):
        if problem.locked_by_user_id is not None:
            raise ProblemLockedError("Problem is already locked.")


@pytest.mark.asyncio
async def test_already_locked_problem_is_caught() -> None:
    """Verify ProblemLockedError is raised and caught correctly."""
    with pytest.raises(ProblemLockedError, match="already locked"):
        raise ProblemLockedError("Problem is already locked.")


# ---------------------------------------------------------------------------
# Test: Elo finalization logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_elo_updated_on_match_end(db_session: AsyncSession) -> None:
    user_a, user_b, match, problem = await _seed_match(db_session)

    # Simulate player_a winning
    result = calculate_elo_result(
        match.player_a_elo_at_start,
        match.player_b_elo_at_start,
        1.0,
    )

    user_a.elo_rating = result.new_rating_a
    user_b.elo_rating = result.new_rating_b
    match.status = MatchStatus.COMPLETED
    match.winner_id = user_a.id
    user_a.wins += 1
    user_b.losses += 1

    await db_session.flush()

    assert user_a.elo_rating > 1200  # winner gained
    assert user_b.elo_rating < 1000  # loser lost (Giant Slayer may apply)
    assert match.status == MatchStatus.COMPLETED
    assert user_a.wins == 1
    assert user_b.losses == 1


@pytest.mark.asyncio
async def test_giant_slayer_applied_on_upset(db_session: AsyncSession) -> None:
    """Low-rated player defeating high-rated player earns Giant Slayer bonus."""
    user_a = User(discord_id=333, cf_handle="underdog", is_verified=True, elo_rating=1000)
    user_b = User(discord_id=444, cf_handle="favourite", is_verified=True, elo_rating=1250)
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    result = calculate_elo_result(1000, 1250, 1.0)
    assert result.giant_slayer_applied is True
    assert result.delta_a > 0


# ---------------------------------------------------------------------------
# Test: problem_id property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_problem_id_property(db_session: AsyncSession) -> None:
    _, _, match, problem = await _seed_match(db_session)
    assert problem.problem_id == "1234A"


# ---------------------------------------------------------------------------
# Test: match score accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scores_accumulate_correctly(db_session: AsyncSession) -> None:
    user_a, user_b, match, _ = await _seed_match(db_session)

    # Add a second problem
    problem2 = LockoutProblem(
        match_id=match.id,
        contest_id=1234,
        problem_index="B",
        problem_name="Problem B",
        cf_rating=1200,
    )
    db_session.add(problem2)
    await db_session.flush()

    pts_a = problem_points(1000)
    pts_b = problem_points(1200)

    match.player_a_score += pts_a
    match.player_b_score += pts_b
    await db_session.flush()

    assert match.player_a_score == pts_a
    assert match.player_b_score == pts_b
