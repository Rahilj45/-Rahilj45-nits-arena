"""Elo rating calculator with Giant Slayer bonus for NITS Arena."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# Standard Elo K-factor
K_FACTOR: int = 32

# Giant Slayer: multiplier applied when the winner is 200+ Elo below the loser
GIANT_SLAYER_THRESHOLD: int = 200
GIANT_SLAYER_MULTIPLIER: float = 1.5


@dataclass(frozen=True)
class EloResult:
    """Outcome of an Elo rating update."""

    new_rating_a: int
    new_rating_b: int
    delta_a: int
    delta_b: int
    giant_slayer_applied: bool


def expected_score(rating_a: int, rating_b: int) -> float:
    """Calculate the expected score for player A against player B.

    Uses the standard Elo expected-score formula:
        E_a = 1 / (1 + 10^((R_b - R_a) / 400))

    Args:
        rating_a: Current Elo rating of player A.
        rating_b: Current Elo rating of player B.

    Returns:
        A float in [0, 1] representing player A's expected score.
    """
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def calculate_elo(
    rating_a: int,
    rating_b: int,
    score_a: float,
) -> Tuple[int, int]:
    """Compute new Elo ratings after a match.

    Applies the Giant Slayer multiplier when the winner's pre-match rating is
    at least *GIANT_SLAYER_THRESHOLD* below the loser's.

    Args:
        rating_a: Pre-match Elo rating of player A (the player being evaluated).
        rating_b: Pre-match Elo rating of player B.
        score_a: Actual outcome for player A: 1.0 = win, 0.5 = draw, 0.0 = loss.

    Returns:
        A tuple ``(new_rating_a, new_rating_b)`` with updated integer ratings.
    """
    e_a: float = expected_score(rating_a, rating_b)
    e_b: float = 1.0 - e_a

    delta_a: float = K_FACTOR * (score_a - e_a)
    delta_b: float = K_FACTOR * ((1.0 - score_a) - e_b)

    # Giant Slayer bonus: winner upset an opponent 200+ Elo higher
    if score_a == 1.0 and (rating_b - rating_a) >= GIANT_SLAYER_THRESHOLD:
        delta_a *= GIANT_SLAYER_MULTIPLIER
    elif score_a == 0.0 and (rating_a - rating_b) >= GIANT_SLAYER_THRESHOLD:
        delta_b *= GIANT_SLAYER_MULTIPLIER

    new_a: int = max(0, rating_a + round(delta_a))
    new_b: int = max(0, rating_b + round(delta_b))
    return new_a, new_b


def calculate_elo_result(
    rating_a: int,
    rating_b: int,
    score_a: float,
) -> EloResult:
    """Compute an Elo update and return a rich :class:`EloResult` object.

    Args:
        rating_a: Pre-match Elo rating of player A.
        rating_b: Pre-match Elo rating of player B.
        score_a: Actual outcome for player A (1.0/0.5/0.0).

    Returns:
        :class:`EloResult` with new ratings, deltas, and Giant Slayer flag.
    """
    giant_slayer = (score_a == 1.0 and (rating_b - rating_a) >= GIANT_SLAYER_THRESHOLD) or (
        score_a == 0.0 and (rating_a - rating_b) >= GIANT_SLAYER_THRESHOLD
    )

    new_a, new_b = calculate_elo(rating_a, rating_b, score_a)
    return EloResult(
        new_rating_a=new_a,
        new_rating_b=new_b,
        delta_a=new_a - rating_a,
        delta_b=new_b - rating_b,
        giant_slayer_applied=giant_slayer,
    )


def get_rank(rating: int) -> str:
    """Return the rank label for the given Elo rating.

    Rank thresholds:
        - Script Kiddie: < 1200
        - Pupil: 1200 – 1399
        - Specialist: 1400 – 1599
        - Expert: 1600 – 1799
        - The Architect: 1800+

    Args:
        rating: Current Elo rating.

    Returns:
        Human-readable rank string.
    """
    if rating < 1200:
        return "Script Kiddie"
    if rating < 1400:
        return "Pupil"
    if rating < 1600:
        return "Specialist"
    if rating < 1800:
        return "Expert"
    return "The Architect"


def problem_points(rating: int) -> int:
    """Return match points awarded for solving a problem of the given CF rating.

    Scaling (linear between anchor points, clamped outside):
        800-rated  → 100 pts
        1200-rated → 300 pts

    Args:
        rating: Codeforces problem rating.

    Returns:
        Integer point value.
    """
    # Linear interpolation: y = 100 + (rating - 800) * (200 / 400)
    pts = 100 + (rating - 800) * (200 / 400)
    return max(100, min(int(pts), 500))
