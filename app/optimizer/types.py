"""Optimizer data types and cut geometry.

Cut geometry (the kerf model)
-----------------------------
Pieces p1..pn cut from stock of length L are laid out as:

    p1 | kerf | p2 | kerf | ... | pn | [rest]

The n-1 cuts *between* pieces always happen. If material remains after
the last piece (rest > 0), one final cut frees pn, consuming another
kerf from the rest; if rest <= kerf the final cut eats it entirely
(saw dust). If the last piece ends exactly at the stock end (rest == 0)
the final cut is not needed.

Therefore:
    feasible      <=>  sum(pieces) + (n-1) * kerf <= L
    leftover      =    max(0, L - sum(pieces) - n * kerf)   (0 when exact fit)
"""

from collections.abc import Callable
from dataclasses import dataclass, field

NEW = "new"
REMNANT = "remnant"


class InfeasibleError(ValueError):
    """Raised when demand cannot possibly be satisfied (piece > stock length)."""


@dataclass(frozen=True)
class ReservedLength:
    """A frequently requested length the optimizer should protect remnants of."""

    length_mm: int
    weight: float = 1.0


@dataclass(frozen=True)
class ScoringConfig:
    weight_scrap: float = 1.0  # penalty per mm of scrap
    # Handling/purchase friction per fresh board, charged ON TOP of the
    # board's embodied material value value_fn(stock_length). None => 0.
    weight_new_board: float | None = 2000.0
    remnant_value_per_mm: float = 0.6
    # Exponent of the convex default value fn: value(l) = per_mm * l * (l/stock)^convexity.
    # Convexity makes long lengths disproportionately valuable, so the
    # optimizer prefers tight fits on short remnants over breaking long stock.
    value_convexity: float = 0.5
    # Optional custom remnant value fn: (length_mm, stock_length_mm) -> float.
    value_fn: Callable[[int, int], float] | None = None
    reserved_lengths: tuple[ReservedLength, ...] = ()
    # Extra worth of a remnant matching a reserved length. For protection
    # to bite (fresh board preferred over breaking a reserved remnant) it
    # must comfortably exceed weight_new_board; if it exceeds it by too
    # much, even an exact-length demand stops using the matching remnant.
    # With the defaults here the workable band is roughly 2500..4100.
    reserved_bonus: float = 3000.0
    reserved_tolerance_mm: int = 50


@dataclass(frozen=True)
class Problem:
    demand: tuple[tuple[int, int], ...]  # (length_mm, quantity)
    remnants: tuple[tuple[int, int], ...]  # (length_mm, count available)
    stock_length_mm: int
    kerf_mm: int
    min_usable_mm: int
    scoring: ScoringConfig = ScoringConfig()
    seed: int = 42


@dataclass
class BoardPlan:
    """Pieces assigned to one physical board (a fresh board or a remnant)."""

    source_kind: str  # NEW | REMNANT
    source_length_mm: int
    pieces: list[int] = field(default_factory=list)

    def used_mm(self, kerf_mm: int) -> int:
        """Length consumed if this is the final state (incl. trailing cut/dust)."""
        return self.source_length_mm - self.leftover_mm(kerf_mm)

    def is_feasible(self, kerf_mm: int) -> bool:
        n = len(self.pieces)
        if n == 0:
            return True
        return sum(self.pieces) + (n - 1) * kerf_mm <= self.source_length_mm

    def leftover_mm(self, kerf_mm: int) -> int:
        n = len(self.pieces)
        if n == 0:
            return self.source_length_mm
        return max(0, self.source_length_mm - sum(self.pieces) - n * kerf_mm)

    def cuts_count(self, kerf_mm: int) -> int:
        n = len(self.pieces)
        if n == 0:
            return 0
        rest = self.source_length_mm - sum(self.pieces) - (n - 1) * kerf_mm
        return n - 1 if rest == 0 else n

    def fits(self, piece_mm: int, kerf_mm: int) -> bool:
        n = len(self.pieces) + 1
        return sum(self.pieces) + piece_mm + (n - 1) * kerf_mm <= self.source_length_mm


@dataclass
class CutPlan:
    """Optimizer output: a full cutting plan plus its score breakdown."""

    boards: list[BoardPlan]
    kerf_mm: int
    min_usable_mm: int
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)

    @property
    def new_boards_used(self) -> int:
        return sum(1 for b in self.boards if b.source_kind == NEW)

    def remnants_consumed(self) -> list[int]:
        return sorted(b.source_length_mm for b in self.boards if b.source_kind == REMNANT)

    def remnants_created(self) -> list[int]:
        return sorted(
            b.leftover_mm(self.kerf_mm)
            for b in self.boards
            if b.pieces and b.leftover_mm(self.kerf_mm) >= self.min_usable_mm
        )

    def scrap_mm(self) -> int:
        return sum(
            b.leftover_mm(self.kerf_mm)
            for b in self.boards
            if b.pieces and b.leftover_mm(self.kerf_mm) < self.min_usable_mm
        )
