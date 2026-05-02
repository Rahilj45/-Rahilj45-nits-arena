"""SQLAlchemy ORM models for NITS Arena."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class User(Base):
    """Represents a Discord user linked to a Codeforces account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    cf_handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    verification_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    elo_rating: Mapped[int] = mapped_column(Integer, default=1200, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draws: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    matches_as_player_a: Mapped[List["Match"]] = relationship(
        "Match", foreign_keys="Match.player_a_id", back_populates="player_a"
    )
    matches_as_player_b: Mapped[List["Match"]] = relationship(
        "Match", foreign_keys="Match.player_b_id", back_populates="player_b"
    )

    def __repr__(self) -> str:
        return f"<User discord_id={self.discord_id} cf_handle={self.cf_handle} elo={self.elo_rating}>"


class Match(Base):
    """Represents a 1v1 lockout match between two players."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_a_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    player_b_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus), default=MatchStatus.PENDING, nullable=False
    )
    winner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    player_a_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    player_b_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Elo ratings captured at match start (Giant Slayer uses these, not live ratings)
    player_a_elo_at_start: Mapped[int] = mapped_column(Integer, nullable=False)
    player_b_elo_at_start: Mapped[int] = mapped_column(Integer, nullable=False)
    min_rating: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    max_rating: Mapped[int] = mapped_column(Integer, default=1600, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    player_a: Mapped["User"] = relationship("User", foreign_keys=[player_a_id], back_populates="matches_as_player_a")
    player_b: Mapped["User"] = relationship("User", foreign_keys=[player_b_id], back_populates="matches_as_player_b")
    winner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[winner_id])
    problems: Mapped[List["LockoutProblem"]] = relationship(
        "LockoutProblem", back_populates="match", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Match id={self.id} status={self.status}>"


class LockoutProblem(Base):
    """A problem assigned to a lockout match, with atomic lock semantics.

    The ``locked_by_user_id`` column implements the "first solver wins" pattern.
    Row-level locking (``SELECT ... FOR UPDATE``) must be used when updating
    this column to prevent the multi-lock race condition.
    """

    __tablename__ = "lockout_problems"
    __table_args__ = (
        UniqueConstraint("match_id", "contest_id", "problem_index", name="uq_match_problem"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("matches.id"), nullable=False
    )
    contest_id: Mapped[int] = mapped_column(Integer, nullable=False)
    problem_index: Mapped[str] = mapped_column(String(8), nullable=False)
    problem_name: Mapped[str] = mapped_column(String(256), nullable=False)
    cf_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL means unclaimed; set atomically via FOR UPDATE lock
    locked_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    points_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    match: Mapped["Match"] = relationship("Match", back_populates="problems")
    locked_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[locked_by_user_id])

    @property
    def problem_id(self) -> str:
        """Human-readable problem identifier, e.g. ``"1234A"``."""
        return f"{self.contest_id}{self.problem_index}"

    def __repr__(self) -> str:
        return (
            f"<LockoutProblem match_id={self.match_id} "
            f"problem={self.problem_id} locked_by={self.locked_by_user_id}>"
        )
