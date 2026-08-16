"""Feasibility validation for (possibly manually edited) plans.

Used both by the optimizer's own output tests and by the plan editor:
every manual adjustment round-trips through validate_plan before it can
be confirmed.
"""

from collections import Counter
from dataclasses import dataclass, field

from app.optimizer.scoring import ScoreContext
from app.optimizer.types import NEW, REMNANT, BoardPlan, Problem


@dataclass
class ValidationResult:
    ok: bool
    board_errors: list[tuple[int, str]] = field(default_factory=list)  # (board idx, message)
    demand_errors: list[str] = field(default_factory=list)
    inventory_errors: list[str] = field(default_factory=list)
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)


def validate_plan(boards: list[BoardPlan], problem: Problem) -> ValidationResult:
    kerf = problem.kerf_mm
    res = ValidationResult(ok=True)

    for i, b in enumerate(boards):
        if b.source_kind not in (NEW, REMNANT):
            res.board_errors.append((i, f"unknown source kind {b.source_kind!r}"))
            continue
        if b.source_kind == NEW and b.source_length_mm != problem.stock_length_mm:
            res.board_errors.append(
                (i, f"new board length {b.source_length_mm} != stock {problem.stock_length_mm}")
            )
        if not b.pieces:
            res.board_errors.append((i, "board has no pieces"))
        if not b.is_feasible(kerf):
            n = len(b.pieces)
            need = sum(b.pieces) + (n - 1) * kerf
            res.board_errors.append(
                (i, f"pieces + kerf need {need} mm but source is {b.source_length_mm} mm")
            )

    planned = Counter(p for b in boards for p in b.pieces)
    demanded = Counter()
    for length, qty in problem.demand:
        demanded[length] += qty
    for length in sorted(demanded.keys() | planned.keys()):
        d, p = demanded[length], planned[length]
        if p < d:
            res.demand_errors.append(f"length {length}: planned {p} of {d} required")
        elif p > d:
            res.demand_errors.append(f"length {length}: planned {p} but only {d} required")

    available = Counter()
    for length, count in problem.remnants:
        available[length] += count
    used = Counter(b.source_length_mm for b in boards if b.source_kind == REMNANT)
    for length in sorted(used):
        if used[length] > available[length]:
            res.inventory_errors.append(
                f"remnant {length} mm: plan uses {used[length]}, inventory has {available[length]}"
            )

    res.ok = not (res.board_errors or res.demand_errors or res.inventory_errors)
    ctx = ScoreContext(
        problem.scoring, problem.stock_length_mm, problem.min_usable_mm, problem.kerf_mm
    )
    res.score = round(ctx.score(boards), 4)
    res.breakdown = ctx.breakdown(boards)
    return res
