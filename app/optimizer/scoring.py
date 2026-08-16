"""Plan scoring.

score(plan) = sum over boards of contribution(board), where

  contribution(board) =
      + worth(leftover)            if leftover >= min_usable  (remnant created)
      - leftover * weight_scrap    if leftover <  min_usable  (scrap)
      - worth(source_length)                    for a remnant source
      - value(stock_length) - weight_new_board  for a fresh board

  worth(l)  = value(l) + reserved_bonus(l)
  value(l)  = remnant_value_per_mm * l * (l / stock_length)^value_convexity
              (convex: long lengths are disproportionately valuable, so
              breaking long stock into fragments destroys value)

Why fresh boards cost value(stock) PLUS a fixed weight_new_board:
convexity alone makes "spread small pieces over many fresh boards to
farm long remnants" only *marginally* worse than packing tightly.
Example (kerf 4, stock 13000, per_mm 0.6, convexity 0.5): demand 2x900
on one board scores -1569, on two boards -1599 -- a 30-point margin that
noise in the value function could flip. The per-board handling cost makes
"fewest boards unless remnant value genuinely justifies it" robust.

Reserved lengths: a length l matches reserved length R when
R <= l <= R + tolerance (an l slightly *shorter* than R cannot serve an
R-length demand, so the match is asymmetric). Matching remnants are worth
extra -- consuming one is penalized, creating one is rewarded.
"""

from app.optimizer.types import NEW, BoardPlan, ScoringConfig


class ScoreContext:
    """Precomputed scoring closure for one problem."""

    def __init__(self, cfg: ScoringConfig, stock_length_mm: int, min_usable_mm: int, kerf_mm: int):
        self.cfg = cfg
        self.stock_length_mm = stock_length_mm
        self.min_usable_mm = min_usable_mm
        self.kerf_mm = kerf_mm
        self.new_board_cost = self.value(stock_length_mm) + (
            cfg.weight_new_board if cfg.weight_new_board is not None else 0.0
        )

    def value(self, length_mm: int) -> float:
        if length_mm <= 0:
            return 0.0
        cfg = self.cfg
        if cfg.value_fn is not None:
            return cfg.value_fn(length_mm, self.stock_length_mm)
        ratio = length_mm / self.stock_length_mm
        return cfg.remnant_value_per_mm * length_mm * (ratio**cfg.value_convexity)

    def reserved_bonus(self, length_mm: int) -> float:
        cfg = self.cfg
        best = 0.0
        for r in cfg.reserved_lengths:
            if r.length_mm <= length_mm <= r.length_mm + cfg.reserved_tolerance_mm:
                best = max(best, cfg.reserved_bonus * r.weight)
        return best

    def worth(self, length_mm: int) -> float:
        return self.value(length_mm) + self.reserved_bonus(length_mm)

    def board_contribution(self, board: BoardPlan) -> float:
        if board.source_kind == NEW:
            out = -self.new_board_cost
        else:
            out = -self.worth(board.source_length_mm)
        if board.pieces:
            leftover = board.leftover_mm(self.kerf_mm)
            if leftover >= self.min_usable_mm:
                out += self.worth(leftover)
            else:
                out -= leftover * self.cfg.weight_scrap
        return out

    def score(self, boards: list[BoardPlan]) -> float:
        return sum(self.board_contribution(b) for b in boards)

    def breakdown(self, boards: list[BoardPlan]) -> dict:
        new_boards = [b for b in boards if b.source_kind == NEW]
        remnant_sources = [b for b in boards if b.source_kind != NEW]
        created, scrap = [], 0
        for b in boards:
            if not b.pieces:
                continue
            leftover = b.leftover_mm(self.kerf_mm)
            if leftover >= self.min_usable_mm:
                created.append(leftover)
            else:
                scrap += leftover
        return {
            "new_boards_used": len(new_boards),
            "new_board_cost": round(len(new_boards) * self.new_board_cost, 2),
            "remnants_consumed": sorted(b.source_length_mm for b in remnant_sources),
            "remnants_consumed_value": round(
                sum(self.worth(b.source_length_mm) for b in remnant_sources), 2
            ),
            "remnants_created": sorted(created),
            "remnants_created_value": round(sum(self.worth(x) for x in created), 2),
            "scrap_mm": scrap,
            "scrap_penalty": round(scrap * self.cfg.weight_scrap, 2),
            "total_cuts": sum(b.cuts_count(self.kerf_mm) for b in boards),
        }
