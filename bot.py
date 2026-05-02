"""NITS Arena – main bot entry point."""

from __future__ import annotations

import os

import disnake
from disnake.ext import commands
from dotenv import load_dotenv

from database.session import close_db, create_tables, init_db
from utils.logger import get_logger

load_dotenv()

logger = get_logger("bot")

# ---------------------------------------------------------------------------
# Bot configuration
# ---------------------------------------------------------------------------

COMMAND_SYNC_FLAGS = commands.CommandSyncFlags.default()
COMMAND_SYNC_FLAGS.sync_commands_debug = True

bot = commands.InteractionBot(
    intents=disnake.Intents.default(),
    command_sync_flags=COMMAND_SYNC_FLAGS,
)

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)  # type: ignore[union-attr]
    logger.info("Guilds: %d", len(bot.guilds))


@bot.event
async def on_slash_command_error(
    inter: disnake.ApplicationCommandInteraction,
    error: Exception,
) -> None:
    """Global fallback error handler for unhandled slash command errors."""
    logger.exception("Unhandled slash command error: %s", error)
    msg = "❌ An unexpected error occurred. The team has been notified."
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------


async def startup() -> None:
    """Initialise database and load cogs before the bot connects."""
    init_db()
    await create_tables()
    bot.load_extension("cogs.verification")
    bot.load_extension("cogs.lockout")
    bot.load_extension("cogs.leaderboard")
    logger.info("All cogs loaded.")


async def shutdown() -> None:
    """Clean up resources when the bot disconnects."""
    await close_db()
    logger.info("Database connections closed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file.")

    import asyncio

    async def main() -> None:
        await startup()
        try:
            await bot.start(token)
        finally:
            await shutdown()

    asyncio.run(main())
