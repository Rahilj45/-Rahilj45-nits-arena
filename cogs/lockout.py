"""Lockout cog: /start-match, /submit-solution, /match-status commands.

Race condition prevention
-------------------------
When a user submits a solution the bot must atomically:

1. Acquire a PostgreSQL row-level lock on the ``lockout_problems`` row
   (``SELECT … FOR UPDATE``).
2. Verify that the row is still unclaimed (``locked_by_user_id IS NULL``).
3. Check Codeforces for a valid accepted submission.
4. Persist the lock and award points – all inside a single transaction.

This prevents the "multi-lock problem" where two concurrent submissions
for the same problem could both see it as unclaimed before either writes.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import List, Optional

import disnake
from disnake.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LockoutProblem, Match, MatchStatus, User
from database.session import get_session
from utils.cf_api import CodeforcesClient
from utils.elo_calculator import (
    calculate_elo_result,
    get_rank,
    problem_points,
)
from utils.exceptions import (
    InvalidSubmissionError,
    MatchAlreadyActiveError,
    MatchNotFoundError,
    ProblemLockedError,
    UserNotFoundError,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PROBLEM_COUNT = 5


class LockoutCog(commands.Cog, name="Lockout"):
    """Commands for managing 1v1 lockout competitive-programming matches."""

    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /start-match
    # ------------------------------------------------------------------

    @commands.slash_command(name="start-match", description="Challenge another user to a 1v1 lockout match.")
    async def start_match(
        self,
        inter: disnake.ApplicationCommandInteraction,
        opponent: disnake.Member = commands.Param(description="The Discord user you want to challenge."),
        min_rating: int = commands.Param(default=800, description="Minimum problem rating (default 800)."),
        max_rating: int = commands.Param(default=1600, description="Maximum problem rating (default 1600)."),
        problem_count: int = commands.Param(
            default=_DEFAULT_PROBLEM_COUNT,
            description=f"Number of problems (default {_DEFAULT_PROBLEM_COUNT}).",
        ),
    ) -> None:
        """Start a lockout match between the invoker and the specified opponent."""
        await inter.response.defer()

        if opponent.id == inter.author.id:
            await inter.followup.send("❌ You cannot challenge yourself.", ephemeral=True)
            return

        async for session in get_session():
            player_a = await _require_verified_user(session, inter.author.id)
            player_b = await _require_verified_user(session, opponent.id)

            # Ensure neither player is in an active match
            await _assert_no_active_match(session, player_a.id)
            await _assert_no_active_match(session, player_b.id)

            # Fetch problems from Codeforces
            async with CodeforcesClient() as cf:
                all_problems = await cf.get_problems(min_rating, max_rating)
                solved_a = set(await cf.get_solved_problems(player_a.cf_handle))
                solved_b = set(await cf.get_solved_problems(player_b.cf_handle))

            # Exclude already-solved problems for either player
            eligible = [
                p
                for p in all_problems
                if f"{p.get('contestId', '')}{p.get('index', '')}" not in solved_a
                and f"{p.get('contestId', '')}{p.get('index', '')}" not in solved_b
            ]

            if len(eligible) < problem_count:
                await inter.followup.send(
                    f"❌ Not enough unsolved problems in the {min_rating}–{max_rating} rating range. "
                    f"Found {len(eligible)}, need {problem_count}.",
                )
                return

            selected = random.sample(eligible, problem_count)

            # Create match
            match = Match(
                player_a_id=player_a.id,
                player_b_id=player_b.id,
                status=MatchStatus.ACTIVE,
                player_a_elo_at_start=player_a.elo_rating,
                player_b_elo_at_start=player_b.elo_rating,
                min_rating=min_rating,
                max_rating=max_rating,
                started_at=datetime.now(timezone.utc),
            )
            session.add(match)
            await session.flush()  # Populate match.id

            for prob in selected:
                lp = LockoutProblem(
                    match_id=match.id,
                    contest_id=prob["contestId"],
                    problem_index=prob["index"],
                    problem_name=prob.get("name", "Unknown"),
                    cf_rating=prob["rating"],
                )
                session.add(lp)

            logger.info(
                "Match %s started: player_a=%s vs player_b=%s",
                match.id,
                player_a.cf_handle,
                player_b.cf_handle,
            )

        embed = _build_match_start_embed(inter.author, opponent, match, selected)
        await inter.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /submit-solution
    # ------------------------------------------------------------------

    @commands.slash_command(
        name="submit-solution",
        description="Claim a lockout problem you have solved on Codeforces.",
    )
    async def submit_solution(
        self,
        inter: disnake.ApplicationCommandInteraction,
        match_id: int = commands.Param(description="The ID of your active match."),
        contest_id: int = commands.Param(description="Codeforces contest ID (e.g. 1234)."),
        problem_index: str = commands.Param(description="Problem index (e.g. A, B, C)."),
    ) -> None:
        """Atomically claim a problem solve using PostgreSQL FOR UPDATE locking."""
        await inter.response.defer(ephemeral=True)

        problem_index = problem_index.upper().strip()
        discord_id = inter.author.id

        async for session in get_session():
            # Validate match and player membership
            match = await _get_active_match(session, match_id)
            user = await _require_verified_user(session, discord_id)

            if user.id not in (match.player_a_id, match.player_b_id):
                raise InvalidSubmissionError("You are not a participant in this match.")

            # -----------------------------------------------------------
            # ATOMIC LOCK PATTERN: SELECT … FOR UPDATE
            # Acquires a row-level lock BEFORE reading locked_by_user_id.
            # This serialises concurrent submissions for the same problem.
            # -----------------------------------------------------------
            result = await session.execute(
                select(LockoutProblem)
                .where(
                    LockoutProblem.match_id == match_id,
                    LockoutProblem.contest_id == contest_id,
                    LockoutProblem.problem_index == problem_index,
                )
                .with_for_update()  # PostgreSQL FOR UPDATE
            )
            problem: Optional[LockoutProblem] = result.scalar_one_or_none()

            if problem is None:
                await inter.followup.send(
                    f"❌ Problem **{contest_id}{problem_index}** is not part of match #{match_id}.",
                    ephemeral=True,
                )
                return

            if problem.locked_by_user_id is not None:
                raise ProblemLockedError(
                    f"Problem **{problem.problem_id}** has already been claimed."
                )

            # -----------------------------------------------------------
            # Verify accepted submission on Codeforces (while lock is held)
            # -----------------------------------------------------------
            async with CodeforcesClient() as cf:
                submissions = await cf.get_user_submissions(user.cf_handle, count=20)

            accepted = any(
                sub.get("verdict") == "OK"
                and sub.get("problem", {}).get("contestId") == contest_id
                and sub.get("problem", {}).get("index") == problem_index
                for sub in submissions
            )

            if not accepted:
                await inter.followup.send(
                    f"❌ No accepted submission found for **{contest_id}{problem_index}** "
                    "on your Codeforces account. Solve it and try again.",
                    ephemeral=True,
                )
                return

            # -----------------------------------------------------------
            # Award points and persist lock (still inside the transaction)
            # -----------------------------------------------------------
            pts = problem_points(problem.cf_rating)
            problem.locked_by_user_id = user.id
            problem.points_awarded = pts
            problem.locked_at = datetime.now(timezone.utc)

            if user.id == match.player_a_id:
                match.player_a_score += pts
            else:
                match.player_b_score += pts

            logger.info(
                "Problem %s locked by user %s in match %s (+%d pts)",
                problem.problem_id,
                user.cf_handle,
                match_id,
                pts,
            )

            # Check if all problems are solved → end match
            all_problems = (
                await session.execute(
                    select(LockoutProblem).where(LockoutProblem.match_id == match_id)
                )
            ).scalars().all()

            match_ended = all(p.locked_by_user_id is not None for p in all_problems)
            if match_ended:
                await _finalize_match(session, match)

        user_score = match.player_a_score if user.id == match.player_a_id else match.player_b_score
        reply_lines = [
            f"✅ Locked **{contest_id}{problem_index}** for **+{pts} pts**!",
            f"Current score — <@{inter.author.id}>: **{user_score}**",
        ]
        if match_ended:
            reply_lines.append("\n🏁 Match over! Check `/match-status` for results.")

        await inter.followup.send("\n".join(reply_lines), ephemeral=True)

    # ------------------------------------------------------------------
    # /match-status
    # ------------------------------------------------------------------

    @commands.slash_command(name="match-status", description="Show the current status of a lockout match.")
    async def match_status(
        self,
        inter: disnake.ApplicationCommandInteraction,
        match_id: int = commands.Param(description="The match ID to inspect."),
    ) -> None:
        """Display a summary of a match, including per-problem lock status."""
        await inter.response.defer()

        async for session in get_session():
            match: Optional[Match] = (
                await session.execute(select(Match).where(Match.id == match_id))
            ).scalar_one_or_none()

            if match is None:
                raise MatchNotFoundError(f"Match #{match_id} not found.")

            player_a: User = (
                await session.execute(select(User).where(User.id == match.player_a_id))
            ).scalar_one()
            player_b: User = (
                await session.execute(select(User).where(User.id == match.player_b_id))
            ).scalar_one()

            problems: List[LockoutProblem] = (
                await session.execute(
                    select(LockoutProblem).where(LockoutProblem.match_id == match_id)
                )
            ).scalars().all()

        embed = _build_match_status_embed(match, player_a, player_b, problems)
        await inter.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    @start_match.error  # type: ignore[arg-type]
    @submit_solution.error  # type: ignore[arg-type]
    @match_status.error  # type: ignore[arg-type]
    async def lockout_error_handler(
        self,
        inter: disnake.ApplicationCommandInteraction,
        error: Exception,
    ) -> None:
        if isinstance(error, (MatchNotFoundError, ProblemLockedError, InvalidSubmissionError)):
            msg = f"❌ {error}"
        elif isinstance(error, MatchAlreadyActiveError):
            msg = f"⚠️ {error}"
        elif isinstance(error, UserNotFoundError):
            msg = "❌ One or more players are not verified. Run `/verify` first."
        else:
            logger.exception("Unexpected error in lockout cog: %s", error)
            msg = "❌ An unexpected error occurred. Please try again later."

        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_verified_user(session: AsyncSession, discord_id: int) -> User:
    user: Optional[User] = (
        await session.execute(select(User).where(User.discord_id == discord_id))
    ).scalar_one_or_none()
    if user is None or not user.is_verified:
        raise UserNotFoundError(
            f"Discord user {discord_id} is not verified. Run `/verify` first."
        )
    return user


async def _assert_no_active_match(session: AsyncSession, user_id: int) -> None:
    active = (
        await session.execute(
            select(Match).where(
                Match.status == MatchStatus.ACTIVE,
                (Match.player_a_id == user_id) | (Match.player_b_id == user_id),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise MatchAlreadyActiveError(
            f"User (id={user_id}) is already in an active match (#{active.id})."
        )


async def _get_active_match(session: AsyncSession, match_id: int) -> Match:
    match: Optional[Match] = (
        await session.execute(
            select(Match).where(Match.id == match_id, Match.status == MatchStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(f"Active match #{match_id} not found.")
    return match


async def _finalize_match(session: AsyncSession, match: Match) -> None:
    """Determine winner, update Elo ratings, and mark match as completed."""
    match.status = MatchStatus.COMPLETED
    match.ended_at = datetime.now(timezone.utc)

    score_a = match.player_a_score
    score_b = match.player_b_score

    if score_a > score_b:
        match.winner_id = match.player_a_id
        score_outcome = 1.0
    elif score_b > score_a:
        match.winner_id = match.player_b_id
        score_outcome = 0.0
    else:
        match.winner_id = None
        score_outcome = 0.5

    # Use Elo ratings captured at match start (Giant Slayer must be deterministic)
    result = calculate_elo_result(
        match.player_a_elo_at_start,
        match.player_b_elo_at_start,
        score_outcome,
    )

    player_a: User = (
        await session.execute(select(User).where(User.id == match.player_a_id))
    ).scalar_one()
    player_b: User = (
        await session.execute(select(User).where(User.id == match.player_b_id))
    ).scalar_one()

    player_a.elo_rating = result.new_rating_a
    player_b.elo_rating = result.new_rating_b

    if score_outcome == 1.0:
        player_a.wins += 1
        player_b.losses += 1
    elif score_outcome == 0.0:
        player_a.losses += 1
        player_b.wins += 1
    else:
        player_a.draws += 1
        player_b.draws += 1

    logger.info(
        "Match %s finalized. Winner=%s, Elo: A %d→%d, B %d→%d, GiantSlayer=%s",
        match.id,
        match.winner_id,
        match.player_a_elo_at_start,
        result.new_rating_a,
        match.player_b_elo_at_start,
        result.new_rating_b,
        result.giant_slayer_applied,
    )


def _build_match_start_embed(
    player_a: disnake.Member,
    player_b: disnake.Member,
    match: "Match",
    problems: list,
) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"⚔️ Lockout Match #{match.id} Started!",
        colour=disnake.Colour.blurple(),
    )
    embed.add_field(name="Player A", value=player_a.mention, inline=True)
    embed.add_field(name="Player B", value=player_b.mention, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    problem_lines = []
    for p in problems:
        pts = problem_points(p["rating"])
        pid = f"{p['contestId']}{p['index']}"
        url = f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}"
        problem_lines.append(f"• [{pid}]({url}) — **{p['name']}** (CF {p['rating']}) → **{pts} pts**")

    embed.add_field(name="📋 Problems", value="\n".join(problem_lines), inline=False)
    embed.set_footer(text=f"Use /submit-solution {match.id} <contestId> <index> to claim a problem.")
    return embed


def _build_match_status_embed(
    match: "Match",
    player_a: "User",
    player_b: "User",
    problems: "List[LockoutProblem]",
) -> disnake.Embed:
    status_emoji = {
        MatchStatus.ACTIVE: "⚔️",
        MatchStatus.COMPLETED: "🏁",
        MatchStatus.CANCELLED: "🚫",
        MatchStatus.PENDING: "⏳",
    }
    colour = {
        MatchStatus.ACTIVE: disnake.Colour.green(),
        MatchStatus.COMPLETED: disnake.Colour.gold(),
        MatchStatus.CANCELLED: disnake.Colour.red(),
        MatchStatus.PENDING: disnake.Colour.greyple(),
    }

    embed = disnake.Embed(
        title=f"{status_emoji.get(match.status, '')} Match #{match.id} — {match.status.value.title()}",
        colour=colour.get(match.status, disnake.Colour.default()),
    )
    embed.add_field(
        name=f"**{player_a.cf_handle}**",
        value=f"Score: **{match.player_a_score}** | Elo: {player_a.elo_rating} [{get_rank(player_a.elo_rating)}]",
        inline=True,
    )
    embed.add_field(
        name=f"**{player_b.cf_handle}**",
        value=f"Score: **{match.player_b_score}** | Elo: {player_b.elo_rating} [{get_rank(player_b.elo_rating)}]",
        inline=True,
    )

    problem_lines = []
    for p in problems:
        if p.locked_by_user_id is None:
            status = "🔓 Unclaimed"
        elif p.locked_by_user_id == match.player_a_id:
            status = f"🔒 **{player_a.cf_handle}** (+{p.points_awarded})"
        else:
            status = f"🔒 **{player_b.cf_handle}** (+{p.points_awarded})"
        problem_lines.append(f"• **{p.problem_id}** — {p.problem_name} (CF {p.cf_rating}) — {status}")

    embed.add_field(name="📋 Problems", value="\n".join(problem_lines) or "None", inline=False)
    return embed


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(LockoutCog(bot))
