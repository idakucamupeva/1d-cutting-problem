"""Exact (CP-SAT) strategy: never worse than the heuristic, same
business rules, deterministic, graceful fallback."""

import pytest

from app.optimizer import NEW, Problem, ReservedLength, ScoringConfig, solve

pytest.importorskip("ortools")

STOCK = 13_000
KERF = 4


def problem(demand, remnants=(), scoring=None, **kw) -> Problem:
    return Problem(
        demand=tuple(demand),
        remnants=tuple(remnants),
        stock_length_mm=STOCK,
        kerf_mm=KERF,
        min_usable_mm=2_000,
        scoring=scoring or ScoringConfig(),
        **kw,
    )


def test_exact_matches_or_beats_heuristic():
    p = problem(
        [(4_700, 3), (3_300, 4), (2_100, 5), (5_900, 2), (1_150, 3)],
        remnants=[(5_000, 2), (7_400, 1), (2_600, 2)],
    )
    heuristic = solve(p, "heuristic")
    exact = solve(p, "exact", exact_timeout_s=6.0)
    assert exact.score >= heuristic.score
    assert exact.breakdown["strategy_used"] in ("exact", "exact_feasible", "heuristic_fallback")
    placed = sorted(x for b in exact.boards for x in b.pieces)
    demanded = sorted(l for l, q in p.demand for _ in range(q))
    assert placed == demanded
    assert all(b.is_feasible(KERF) for b in exact.boards)


def test_exact_finds_perfect_packing():
    exact = solve(problem([(4_000, 6), (4_992, 3)]), "exact", exact_timeout_s=6.0)
    assert exact.new_boards_used == 3
    assert exact.scrap_mm() == 0
    assert exact.breakdown["strategy_used"] == "exact"


def test_exact_respects_reserved_remnant():
    scoring = ScoringConfig(reserved_lengths=(ReservedLength(5_000),))
    exact = solve(
        problem([(4_000, 1)], remnants=[(5_000, 1)], scoring=scoring),
        "exact",
        exact_timeout_s=6.0,
    )
    assert len(exact.boards) == 1
    assert exact.boards[0].source_kind == NEW  # 5.0 m remnant kept intact


def test_exact_is_deterministic():
    p = problem([(4_000, 3), (2_750, 5), (1_800, 4)], remnants=[(5_000, 2)], seed=7)
    a = solve(p, "exact", exact_timeout_s=6.0)
    b = solve(p, "exact", exact_timeout_s=6.0)
    assert [(x.source_kind, x.source_length_mm, x.pieces) for x in a.boards] == [
        (x.source_kind, x.source_length_mm, x.pieces) for x in b.boards
    ]
    assert a.score == b.score


def test_oversize_instance_falls_back_to_heuristic():
    p = problem([(1_000, 100)])
    plan = solve(p, "exact", exact_max_pieces=60)
    assert plan.breakdown["strategy_used"] == "heuristic_fallback"
    assert sum(len(b.pieces) for b in plan.boards) == 100
