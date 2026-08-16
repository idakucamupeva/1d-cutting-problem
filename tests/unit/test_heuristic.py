"""Heuristic solver behavior: thresholds, the 5.0 m remnant scenario,
remnant counts, determinism."""

import pytest

from app.optimizer import (
    NEW,
    REMNANT,
    InfeasibleError,
    Problem,
    ReservedLength,
    ScoringConfig,
    solve,
)

STOCK = 13_000
KERF = 4
MIN_USABLE = 2_000


def problem(demand, remnants=(), scoring=None, **kw) -> Problem:
    return Problem(
        demand=tuple(demand),
        remnants=tuple(remnants),
        stock_length_mm=STOCK,
        kerf_mm=KERF,
        min_usable_mm=MIN_USABLE,
        scoring=scoring or ScoringConfig(),
        **kw,
    )


def placed_pieces(plan) -> list[int]:
    return sorted(p for b in plan.boards for p in b.pieces)


class TestBasics:
    def test_all_demand_is_placed(self):
        plan = solve(problem([(4_000, 3), (2_500, 4)]))
        assert placed_pieces(plan) == [2_500] * 4 + [4_000] * 3

    def test_every_board_is_feasible(self):
        plan = solve(problem([(3_333, 7), (1_200, 5)]))
        assert all(b.is_feasible(KERF) for b in plan.boards)

    def test_piece_longer_than_stock_raises(self):
        with pytest.raises(InfeasibleError):
            solve(problem([(13_001, 1)]))

    def test_empty_demand_gives_empty_plan(self):
        plan = solve(problem([]))
        assert plan.boards == []
        assert plan.score == 0.0


class TestThresholds:
    def test_leftover_at_min_usable_is_remnant(self):
        # 13000 - 10996 - 4 = 2000 == MIN_USABLE -> usable remnant, not scrap.
        plan = solve(problem([(10_996, 1)]))
        assert plan.remnants_created() == [2_000]
        assert plan.scrap_mm() == 0

    def test_leftover_below_min_usable_is_scrap(self):
        # 13000 - 10997 - 4 = 1999 < MIN_USABLE -> scrap.
        plan = solve(problem([(10_997, 1)]))
        assert plan.remnants_created() == []
        assert plan.scrap_mm() == 1_999


class TestRemnantScenario:
    """The business-critical case from the brief."""

    def test_reserved_5m_remnant_is_kept_intact(self):
        # Need 4.0 m; a 5.0 m remnant exists but 5.0 m is a frequent demand.
        # Cutting from it would leave ~1 m of scrap AND break a reserved
        # length -> cut from a fresh board instead (leaves 9.0 m remnant).
        scoring = ScoringConfig(reserved_lengths=(ReservedLength(5_000),))
        plan = solve(problem([(4_000, 1)], remnants=[(5_000, 1)], scoring=scoring))
        assert len(plan.boards) == 1
        assert plan.boards[0].source_kind == NEW
        assert plan.remnants_consumed() == []
        assert plan.remnants_created() == [13_000 - 4_000 - KERF]

    def test_unreserved_good_fit_uses_the_remnant(self):
        # Need 4.9 m; 5.0 m remnant, not reserved: tight fit -> use it,
        # don't break a fresh board.
        plan = solve(problem([(4_900, 1)], remnants=[(5_000, 1)]))
        assert len(plan.boards) == 1
        assert plan.boards[0].source_kind == REMNANT
        assert plan.boards[0].source_length_mm == 5_000

    def test_reserved_remnant_still_used_for_exact_match(self):
        # Need exactly 5.0 m and a reserved 5.0 m remnant exists: using it
        # IS serving the frequent demand -- no reason to protect it.
        scoring = ScoringConfig(reserved_lengths=(ReservedLength(5_000),))
        plan = solve(problem([(5_000, 1)], remnants=[(5_000, 1)], scoring=scoring))
        assert len(plan.boards) == 1
        assert plan.boards[0].source_kind == REMNANT


class TestInventoryConstraints:
    def test_remnant_counts_are_respected(self):
        # Two 5.0 m remnants cannot serve three 4.9 m pieces.
        plan = solve(problem([(4_900, 3)], remnants=[(5_000, 2)]))
        remnant_sources = [b for b in plan.boards if b.source_kind == REMNANT]
        assert len(remnant_sources) <= 2

    def test_remnant_source_lengths_exist_in_pool(self):
        plan = solve(problem([(3_000, 5)], remnants=[(4_000, 1), (7_500, 1)]))
        for b in plan.boards:
            if b.source_kind == REMNANT:
                assert b.source_length_mm in (4_000, 7_500)


class TestDeterminism:
    def test_same_seed_same_plan(self):
        demand = [(4_000, 3), (2_750, 5), (1_800, 4), (5_500, 2)]
        remnants = [(5_000, 2), (3_200, 1), (8_000, 1)]
        a = solve(problem(demand, remnants, seed=7))
        b = solve(problem(demand, remnants, seed=7))
        assert [(x.source_kind, x.source_length_mm, x.pieces) for x in a.boards] == [
            (x.source_kind, x.source_length_mm, x.pieces) for x in b.boards
        ]
        assert a.score == b.score


class TestQuality:
    def test_perfect_packing_is_found(self):
        # 3 x (4000 + 4000 + 4992) fills 3 boards exactly (kerf included).
        plan = solve(problem([(4_000, 6), (4_992, 3)]))
        assert plan.new_boards_used == 3
        assert plan.scrap_mm() == 0

    def test_prefers_filling_open_board_over_new_board(self):
        # 2x6000 + 900 fit on one board (12908 used); a naive one-piece-per-
        # board plan would waste a whole extra board.
        plan = solve(problem([(6_000, 2), (900, 1)]))
        assert plan.new_boards_used == 1
