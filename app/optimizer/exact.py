"""Exact/near-exact strategy via CP-SAT (Google OR-Tools).

Model: one binary x[p,u] per (piece, stock unit); stock units are every
available remnant plus heuristic_new_boards + 2 fresh boards. Feasibility
per unit u with length L: sum(len*x) + kerf*(n-1) <= L, written as
sum((len+kerf)*x) <= (L+kerf)*used.

Objective: the business score from scoring.py. The leftover worth
(convex value + reserved bonus, or scrap penalty below min_usable) is
nonlinear, so it is precomputed as a lookup table at 10 mm resolution
and attached with AddElement on leftover//10. leftover is defined as
max(0, L*used - sum(len*x) - kerf*n), which is conveniently 0 for
unused units, so their table contribution vanishes.

The CP-SAT solution is re-scored with the TRUE ScoreContext and compared
against the heuristic plan; the better plan wins. Fallbacks to the
heuristic: OR-Tools missing, instance above max_pieces, or no feasible
solution within the timeout. breakdown["strategy_used"] records what
actually happened. Deterministic: num_workers=1, random_seed from the
problem.
"""


from app.optimizer.heuristic import solve_heuristic
from app.optimizer.scoring import ScoreContext
from app.optimizer.types import NEW, REMNANT, BoardPlan, CutPlan, Problem

_RES = 10  # mm per table bucket
_SCALE = 100  # float score -> int objective


def solve_exact(
    problem: Problem, max_pieces: int = 60, timeout_s: float = 10.0
) -> CutPlan:
    heuristic = solve_heuristic(problem)
    heuristic.breakdown["strategy_used"] = "heuristic_fallback"

    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return heuristic

    pieces = [
        length for length, qty in sorted(problem.demand, reverse=True) for _ in range(qty)
    ]
    if not pieces or len(pieces) > max_pieces:
        return heuristic

    ctx = ScoreContext(
        problem.scoring, problem.stock_length_mm, problem.min_usable_mm, problem.kerf_mm
    )
    kerf = problem.kerf_mm

    # Stock units: every remnant individually, then fresh boards.
    units: list[tuple[str, int]] = []
    for length, count in sorted(problem.remnants, reverse=True):
        units.extend((REMNANT, length) for _ in range(count))
    n_fresh = min(heuristic.new_boards_used + 2, len(pieces))
    units.extend((NEW, problem.stock_length_mm) for _ in range(n_fresh))

    model = cp_model.CpModel()
    x = [
        [model.new_bool_var(f"x_{p}_{u}") for u in range(len(units))]
        for p in range(len(pieces))
    ]
    used = [model.new_bool_var(f"used_{u}") for u in range(len(units))]

    for p, length in enumerate(pieces):
        model.add(sum(x[p]) == 1)
        for u, (_kind, cap) in enumerate(units):
            if length > cap:
                model.add(x[p][u] == 0)

    terms = []
    for u, (kind, cap) in enumerate(units):
        load = sum((pieces[p] + kerf) * x[p][u] for p in range(len(pieces)))
        model.add(load <= (cap + kerf) * used[u])
        for p in range(len(pieces)):
            model.add(x[p][u] <= used[u])

        leftover = model.new_int_var(0, cap, f"lv_{u}")
        model.add_max_equality(leftover, [cap * used[u] - load, 0])
        idx = model.new_int_var(0, cap // _RES, f"idx_{u}")
        model.add_division_equality(idx, leftover, _RES)

        table = []
        for i in range(cap // _RES + 1):
            l = i * _RES
            if l >= problem.min_usable_mm:
                table.append(round(_SCALE * ctx.worth(l)))
            else:
                table.append(round(-_SCALE * problem.scoring.weight_scrap * l))
        worth_var = model.new_int_var(min(table), max(table), f"worth_{u}")
        model.add_element(idx, table, worth_var)
        terms.append(worth_var)

        source_cost = (
            ctx.new_board_cost if kind == NEW else ctx.worth(cap)
        )
        terms.append(-round(_SCALE * source_cost) * used[u])

    # Symmetry breaking between identical units.
    for u in range(len(units) - 1):
        if units[u] == units[u + 1]:
            model.add(used[u] >= used[u + 1])

    model.maximize(sum(terms))

    solver = cp_model.CpSolver()
    # Deterministic time is the binding limit so interrupted searches stop
    # at a reproducible point; wall clock is only a generous safety stop.
    solver.parameters.max_deterministic_time = timeout_s
    solver.parameters.max_time_in_seconds = timeout_s * 5
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = problem.seed
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return heuristic

    boards: list[BoardPlan] = []
    for u, (kind, cap) in enumerate(units):
        assigned = sorted(
            (pieces[p] for p in range(len(pieces)) if solver.value(x[p][u])), reverse=True
        )
        if assigned:
            boards.append(BoardPlan(source_kind=kind, source_length_mm=cap, pieces=assigned))
    boards.sort(key=lambda b: (b.source_kind == NEW, -b.source_length_mm, b.pieces))

    exact_plan = CutPlan(
        boards=boards,
        kerf_mm=kerf,
        min_usable_mm=problem.min_usable_mm,
        score=round(ctx.score(boards), 4),
        breakdown=ctx.breakdown(boards),
    )
    exact_plan.breakdown["strategy_used"] = (
        "exact" if status == cp_model.OPTIMAL else "exact_feasible"
    )

    # The table is a 10 mm approximation: keep whichever plan truly scores best.
    if heuristic.score > exact_plan.score:
        return heuristic
    return exact_plan
