"""Leaderboard cog: /leaderboard and /profile commands."""

from __future__ import annotations

from typing import List, Optional

import disnake
from disnake.ext import commands
from sqlalchemy import select

from database.models import User
from database.session import get_session
from utils.elo_calculator import get_rank
from utils.exceptions import UserNotFoundError
from utils.logger import get_logger

logger = get_logger(__name__)

_PAGE_SIZE = 10


class LeaderboardCog(commands.Cog, name="Leaderboard"):
    """Commands for viewing rankings and user statistics."""

    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /leaderboard
    # ------------------------------------------------------------------

    @commands.slash_command(name="leaderboard", description="Show the top-ranked players on NITS Arena.")
    async def leaderboard(
        self,
        inter: disnake.ApplicationCommandInteraction,
        page: int = commands.Param(default=1, description="Page number (default 1)."),
    ) -> None:
        """Display a paginated Elo leaderboard."""
        await inter.response.defer()

        offset = (page - 1) * _PAGE_SIZE

        async for session in get_session():
            rows: List[User] = (
                await session.execute(
                    select(User)
                    .where(User.is_verified.is_(True))
                    .order_by(User.elo_rating.desc())
                    .offset(offset)
                    .limit(_PAGE_SIZE)
                )
            ).scalars().all()

            from sqlalchemy import func as sqlfunc
            total_count: int = (
                await session.execute(
                    select(sqlfunc.count()).select_from(User).where(User.is_verified.is_(True))
                )
            ).scalar_one()

        if not rows:
            await inter.followup.send("📋 No ranked players yet.", ephemeral=True)
            return

        embed = disnake.Embed(
            title="🏆 NITS Arena Leaderboard",
            colour=disnake.Colour.gold(),
        )

        lines = []
        for rank_pos, user in enumerate(rows, start=offset + 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank_pos, f"**#{rank_pos}**")
            total_games = user.wins + user.losses + user.draws
            lines.append(
                f"{medal} **{user.cf_handle}** — Elo {user.elo_rating} "
                f"[{get_rank(user.elo_rating)}] | {user.wins}W/{user.losses}L/{user.draws}D"
                f" ({total_games} games)"
            )

        embed.description = "\n".join(lines)
        max_pages = (total_count + _PAGE_SIZE - 1) // _PAGE_SIZE
        embed.set_footer(text=f"Page {page}/{max_pages} — {total_count} ranked players")
        await inter.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /profile
    # ------------------------------------------------------------------

    @commands.slash_command(name="profile", description="Show stats for a NITS Arena player.")
    async def profile(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: Optional[disnake.Member] = commands.Param(
            default=None,
            description="The Discord member to look up (defaults to yourself).",
        ),
    ) -> None:
        """Display the Elo rating, rank, and match record for a user."""
        await inter.response.defer()

        target = member or inter.author
        discord_id = target.id

        async for session in get_session():
            user: Optional[User] = (
                await session.execute(select(User).where(User.discord_id == discord_id))
            ).scalar_one_or_none()

        if user is None or not user.is_verified:
            raise UserNotFoundError(
                f"{target.display_name} has not verified their Codeforces account."
            )

        total_games = user.wins + user.losses + user.draws
        win_rate = (user.wins / total_games * 100) if total_games else 0.0

        embed = disnake.Embed(
            title=f"🎮 {user.cf_handle}'s Profile",
            colour=disnake.Colour.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Elo Rating", value=str(user.elo_rating), inline=True)
        embed.add_field(name="Rank", value=get_rank(user.elo_rating), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Wins", value=str(user.wins), inline=True)
        embed.add_field(name="Losses", value=str(user.losses), inline=True)
        embed.add_field(name="Draws", value=str(user.draws), inline=True)
        embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
        embed.add_field(name="Total Games", value=str(total_games), inline=True)
        embed.add_field(
            name="CF Profile",
            value=f"[{user.cf_handle}](https://codeforces.com/profile/{user.cf_handle})",
            inline=False,
        )
        await inter.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    @leaderboard.error  # type: ignore[arg-type]
    @profile.error  # type: ignore[arg-type]
    async def leaderboard_error_handler(
        self,
        inter: disnake.ApplicationCommandInteraction,
        error: Exception,
    ) -> None:
        if isinstance(error, UserNotFoundError):
            msg = f"❌ {error}"
        else:
            logger.exception("Unexpected error in leaderboard cog: %s", error)
            msg = "❌ An unexpected error occurred. Please try again later."

        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(LeaderboardCog(bot))
