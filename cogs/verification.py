"""Verification cog: /verify command and UUID management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import disnake
from disnake.ext import commands
from sqlalchemy import select

from database.models import User
from database.session import get_session
from utils.cf_api import CodeforcesClient
from utils.exceptions import (
    AlreadyVerifiedError,
    HandleNotFoundError,
    UUIDMismatchError,
)
from utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class VerificationCog(commands.Cog, name="Verification"):
    """Handles Codeforces identity verification via the Organisation field method."""

    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /verify
    # ------------------------------------------------------------------

    @commands.slash_command(name="verify", description="Link your Codeforces account to NITS Arena.")
    async def verify(
        self,
        inter: disnake.ApplicationCommandInteraction,
        handle: str = commands.Param(description="Your Codeforces handle (username)."),
    ) -> None:
        """Link a Discord account to a Codeforces account via UUID verification.

        Steps:
        1. Bot generates a UUID and stores it against the user.
        2. User pastes the UUID into their CF profile's *Organisation* field.
        3. User runs ``/confirm-verification`` to complete the process.
        """
        await inter.response.defer(ephemeral=True)

        discord_id = inter.author.id

        async for session in get_session():
            # Check whether already verified
            existing: User | None = (
                await session.execute(select(User).where(User.discord_id == discord_id))
            ).scalar_one_or_none()

            if existing and existing.is_verified:
                raise AlreadyVerifiedError(
                    f"Your account is already verified as **{existing.cf_handle}**."
                )

            # Validate handle exists on CF
            async with CodeforcesClient() as cf:
                try:
                    await cf.get_user_info(handle)
                except HandleNotFoundError:
                    await inter.followup.send(
                        f"❌ Codeforces handle **{handle}** not found. Please check and try again.",
                        ephemeral=True,
                    )
                    return

            # Generate UUID
            verification_token = str(uuid.uuid4())

            if existing:
                existing.cf_handle = handle
                existing.verification_uuid = verification_token
                existing.is_verified = False
            else:
                user = User(
                    discord_id=discord_id,
                    cf_handle=handle,
                    verification_uuid=verification_token,
                )
                session.add(user)

            logger.info("Generated verification UUID for discord_id=%s handle=%s", discord_id, handle)

        embed = disnake.Embed(
            title="🔐 Verify Your Codeforces Account",
            description=(
                f"To verify ownership of **{handle}**, follow these steps:\n\n"
                f"1. Go to [your Codeforces profile settings](https://codeforces.com/settings/social).\n"
                f"2. Paste the token below into the **Organisation** field and save.\n"
                f"3. Run `/confirm-verification` to complete the process.\n\n"
                f"**Your Verification Token:**\n```\n{verification_token}\n```"
            ),
            colour=disnake.Colour.orange(),
        )
        embed.set_footer(text="Token expires in 30 minutes.")
        await inter.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /confirm-verification
    # ------------------------------------------------------------------

    @commands.slash_command(
        name="confirm-verification",
        description="Confirm your Codeforces account verification after updating your Organisation field.",
    )
    async def confirm_verification(
        self,
        inter: disnake.ApplicationCommandInteraction,
    ) -> None:
        """Check the CF profile and mark the account as verified."""
        await inter.response.defer(ephemeral=True)

        discord_id = inter.author.id

        async for session in get_session():
            user: User | None = (
                await session.execute(select(User).where(User.discord_id == discord_id))
            ).scalar_one_or_none()

            if not user or not user.verification_uuid:
                await inter.followup.send(
                    "❌ No pending verification found. Run `/verify` first.",
                    ephemeral=True,
                )
                return

            if user.is_verified:
                await inter.followup.send(
                    f"✅ Already verified as **{user.cf_handle}**.",
                    ephemeral=True,
                )
                return

            # Check CF Organisation field
            async with CodeforcesClient() as cf:
                try:
                    found = await cf.verify_uuid_in_org(user.cf_handle, user.verification_uuid)
                except HandleNotFoundError:
                    await inter.followup.send(
                        "❌ Could not find your Codeforces handle. Please restart with `/verify`.",
                        ephemeral=True,
                    )
                    return

            if not found:
                raise UUIDMismatchError(
                    "The verification token was not found in your Codeforces Organisation field."
                )

            user.is_verified = True
            user.verification_uuid = None  # Clear token after successful verification
            logger.info("Verified discord_id=%s as cf_handle=%s", discord_id, user.cf_handle)

        await inter.followup.send(
            f"✅ Successfully verified as **{user.cf_handle}**! You can now participate in matches.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    @verify.error  # type: ignore[arg-type]
    @confirm_verification.error  # type: ignore[arg-type]
    async def verification_error_handler(
        self,
        inter: disnake.ApplicationCommandInteraction,
        error: Exception,
    ) -> None:
        """Surface friendly error messages for known exception types."""
        if isinstance(error, AlreadyVerifiedError):
            msg = f"⚠️ {error}"
        elif isinstance(error, UUIDMismatchError):
            msg = (
                "❌ Verification failed: the token was not found in your Codeforces "
                "Organisation field. Double-check that you saved it and try again."
            )
        else:
            logger.exception("Unexpected error in verification cog: %s", error)
            msg = "❌ An unexpected error occurred. Please try again later."

        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(VerificationCog(bot))
