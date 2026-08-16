"""Pure 1D cutting-stock optimizer.

No imports from the web/DB layers. All lengths are integer millimeters.
Deterministic for a given (Problem, seed).
"""

from app.optimizer.exact import solve_exact
from app.optimizer.heuristic import solve_heuristic
from app.optimizer.types import (
    NEW,
    REMNANT,
    BoardPlan,
    CutPlan,
    InfeasibleError,
    Problem,
    ReservedLength,
    ScoringConfig,
)
from app.optimizer.validate import ValidationResult, validate_plan


def solve(
    problem: Problem,
    strategy: str = "heuristic",
    exact_max_pieces: int = 60,
    exact_timeout_s: float = 10.0,
) -> CutPlan:
    """Solve a cutting problem with the given strategy.

    "heuristic" (default): score-aware BFD + local search.
    "exact": CP-SAT, falling back to the heuristic when OR-Tools is
    unavailable, the instance exceeds exact_max_pieces, or the timeout
    passes without a solution; breakdown["strategy_used"] tells which.
    """
    if strategy == "heuristic":
        return solve_heuristic(problem)
    if strategy == "exact":
        return solve_exact(problem, max_pieces=exact_max_pieces, timeout_s=exact_timeout_s)
    raise ValueError(f"Unknown strategy: {strategy}")


__all__ = [
    "NEW",
    "REMNANT",
    "BoardPlan",
    "CutPlan",
    "InfeasibleError",
    "Problem",
    "ReservedLength",
    "ScoringConfig",
    "ValidationResult",
    "solve",
    "solve_exact",
    "solve_heuristic",
    "validate_plan",
]
