"""Plan validation: the gate for manual plan edits."""

from app.optimizer import NEW, REMNANT, BoardPlan, Problem, validate_plan

STOCK = 13_000
KERF = 4


def problem(demand, remnants=()) -> Problem:
    return Problem(
        demand=tuple(demand),
        remnants=tuple(remnants),
        stock_length_mm=STOCK,
        kerf_mm=KERF,
        min_usable_mm=2_000,
    )


def test_valid_plan_passes():
    boards = [BoardPlan(NEW, STOCK, [4_000, 4_000])]
    res = validate_plan(boards, problem([(4_000, 2)]))
    assert res.ok
    assert res.breakdown["new_boards_used"] == 1


def test_overfull_board_is_reported():
    boards = [BoardPlan(NEW, STOCK, [7_000, 7_000])]
    res = validate_plan(boards, problem([(7_000, 2)]))
    assert not res.ok
    assert any("14004" in msg for _, msg in res.board_errors)


def test_missing_and_extra_pieces_are_reported():
    boards = [BoardPlan(NEW, STOCK, [4_000, 3_000])]
    res = validate_plan(boards, problem([(4_000, 2)]))
    assert not res.ok
    assert any("planned 1 of 2" in e for e in res.demand_errors)
    assert any("3000" in e and "0 required" in e for e in res.demand_errors)


def test_overused_remnants_are_reported():
    boards = [
        BoardPlan(REMNANT, 5_000, [4_000]),
        BoardPlan(REMNANT, 5_000, [4_000]),
    ]
    res = validate_plan(boards, problem([(4_000, 2)], remnants=[(5_000, 1)]))
    assert not res.ok
    assert any("uses 2" in e and "has 1" in e for e in res.inventory_errors)


def test_manual_edit_scenario_forced_remnant_ok():
    # User forces the 5.0 m remnant even though the optimizer would not:
    # feasible -> valid, and the breakdown shows the resulting scrap.
    boards = [BoardPlan(REMNANT, 5_000, [4_000])]
    res = validate_plan(boards, problem([(4_000, 1)], remnants=[(5_000, 1)]))
    assert res.ok
    assert res.breakdown["scrap_mm"] == 5_000 - 4_000 - KERF
