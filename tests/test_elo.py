"""Unit tests for utils/elo_calculator.py."""

from __future__ import annotations

import pytest

from utils.elo_calculator import (
    GIANT_SLAYER_THRESHOLD,
    EloResult,
    calculate_elo,
    calculate_elo_result,
    expected_score,
    get_rank,
    problem_points,
)


# ---------------------------------------------------------------------------
# expected_score
# ---------------------------------------------------------------------------


class TestExpectedScore:
    def test_equal_ratings_gives_half(self) -> None:
        assert expected_score(1000, 1000) == pytest.approx(0.5)

    def test_higher_rating_gives_more_than_half(self) -> None:
        assert expected_score(1400, 1000) > 0.5

    def test_lower_rating_gives_less_than_half(self) -> None:
        assert expected_score(1000, 1400) < 0.5

    def test_result_within_bounds(self) -> None:
        score = expected_score(800, 2000)
        assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# calculate_elo
# ---------------------------------------------------------------------------


class TestCalculateElo:
    def test_winner_gains_loser_loses(self) -> None:
        new_a, new_b = calculate_elo(1200, 1200, 1.0)
        assert new_a > 1200
        assert new_b < 1200

    def test_draw_near_equal_ratings_stays_close(self) -> None:
        new_a, new_b = calculate_elo(1200, 1200, 0.5)
        assert new_a == 1200
        assert new_b == 1200

    def test_sum_of_deltas_is_zero_for_symmetric_ratings(self) -> None:
        """Without Giant Slayer, total rating should be conserved (±1 rounding)."""
        new_a, new_b = calculate_elo(1500, 1500, 1.0)
        total_before = 1500 + 1500
        total_after = new_a + new_b
        assert abs(total_after - total_before) <= 1

    def test_rating_cannot_go_below_zero(self) -> None:
        new_a, new_b = calculate_elo(0, 3000, 0.0)
        assert new_a >= 0
        assert new_b >= 0

    def test_giant_slayer_applied_when_upset(self) -> None:
        """Winner rated 200+ below opponent should gain more than K_FACTOR."""
        low_rating = 1000
        high_rating = low_rating + GIANT_SLAYER_THRESHOLD  # exactly at threshold
        new_a, _new_b = calculate_elo(low_rating, high_rating, 1.0)
        normal_a, _ = calculate_elo(low_rating, high_rating - 1, 1.0)
        # With Giant Slayer the delta should be larger
        assert (new_a - low_rating) > (normal_a - low_rating)

    def test_giant_slayer_not_applied_below_threshold(self) -> None:
        """Giant Slayer should NOT apply when gap is < 200."""
        new_a_below, _ = calculate_elo(1000, 1199, 1.0)  # gap = 199
        new_a_at, _ = calculate_elo(1000, 1200, 1.0)     # gap = 200 → Giant Slayer
        assert new_a_at > new_a_below


# ---------------------------------------------------------------------------
# calculate_elo_result
# ---------------------------------------------------------------------------


class TestCalculateEloResult:
    def test_returns_elo_result_type(self) -> None:
        result = calculate_elo_result(1200, 1200, 1.0)
        assert isinstance(result, EloResult)

    def test_deltas_consistent_with_new_ratings(self) -> None:
        result = calculate_elo_result(1300, 1100, 1.0)
        assert result.new_rating_a == 1300 + result.delta_a
        assert result.new_rating_b == 1100 + result.delta_b

    def test_giant_slayer_flag_set(self) -> None:
        result = calculate_elo_result(1000, 1200, 1.0)
        assert result.giant_slayer_applied is True

    def test_giant_slayer_flag_not_set_for_loser(self) -> None:
        """Giant Slayer should not apply when the underdog loses."""
        result = calculate_elo_result(1000, 1200, 0.0)
        assert result.giant_slayer_applied is False

    def test_giant_slayer_flag_not_set_equal_ratings(self) -> None:
        result = calculate_elo_result(1200, 1200, 1.0)
        assert result.giant_slayer_applied is False


# ---------------------------------------------------------------------------
# get_rank
# ---------------------------------------------------------------------------


class TestGetRank:
    @pytest.mark.parametrize(
        "rating,expected",
        [
            (0, "Script Kiddie"),
            (1199, "Script Kiddie"),
            (1200, "Pupil"),
            (1399, "Pupil"),
            (1400, "Specialist"),
            (1599, "Specialist"),
            (1600, "Expert"),
            (1799, "Expert"),
            (1800, "The Architect"),
            (9999, "The Architect"),
        ],
    )
    def test_rank_boundaries(self, rating: int, expected: str) -> None:
        assert get_rank(rating) == expected


# ---------------------------------------------------------------------------
# problem_points
# ---------------------------------------------------------------------------


class TestProblemPoints:
    def test_800_rated_gives_100(self) -> None:
        assert problem_points(800) == 100

    def test_1200_rated_gives_300(self) -> None:
        assert problem_points(1200) == 300

    def test_points_increase_with_rating(self) -> None:
        assert problem_points(900) > problem_points(800)
        assert problem_points(1100) > problem_points(900)

    def test_minimum_clamped(self) -> None:
        assert problem_points(100) == 100  # below 800

    def test_maximum_clamped(self) -> None:
        # Very high rating should not exceed 500
        assert problem_points(9999) <= 500
