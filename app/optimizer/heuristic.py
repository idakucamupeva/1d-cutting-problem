"""Score-aware constructive heuristic + local search.

Construction: pieces sorted descending; each piece is placed where the
*marginal score delta* (via ScoreContext.board_contribution) is best,
choosing among: open boards with room, opening any available remnant, or
opening a fresh board. This is Best-Fit-Decreasing where "best" is the
business score, not tightest fit -- which is what makes remnant
protection work during construction, not only in local search.

Local search: seeded hill climb over three move types:
  - relocate: move one piece to the best alternative board/source
  - swap: exchange two pieces between boards
  - repack: take two boards' pieces and enumerate all subset splits
    between the two sources (capped), keeping the best
Deterministic for a given problem + seed.
"""

import random
from collections import Counter
from itertools import combinations

from app.optimizer.scoring import ScoreContext
from app.optimizer.types import (
    NEW,
    REMNANT,
    BoardPlan,
    CutPlan,
    InfeasibleError,
    Problem,
)

_REPACK_MAX_PIECES = 12


def solve_heuristic(problem: Problem, iterations: int = 2_000) -> CutPlan:
    ctx = ScoreContext(
        problem.scoring, problem.stock_length_mm, problem.min_usable_mm, problem.kerf_mm
    )
    pieces = [
        length for length, qty in sorted(problem.demand, reverse=True) for _ in range(qty)
    ]
    too_long = sorted({p for p in pieces if p > problem.stock_length_mm})
    if too_long:
        raise InfeasibleError(
            f"Pieces longer than stock length {problem.stock_length_mm}: {too_long}"
        )

    pool: Counter[int] = Counter()
    for length, count in problem.remnants:
        pool[length] += count

    boards = _construct(pieces, pool, problem, ctx)
    boards = _local_search(boards, pool, problem, ctx, iterations)

    # Stable presentation order: fresh boards last, big sources first.
    boards.sort(key=lambda b: (b.source_kind == NEW, -b.source_length_mm, b.pieces))
    plan = CutPlan(
        boards=boards,
        kerf_mm=problem.kerf_mm,
        min_usable_mm=problem.min_usable_mm,
        score=round(ctx.score(boards), 4),
        breakdown=ctx.breakdown(boards),
    )
    return plan


def _construct(
    pieces: list[int], pool: Counter, problem: Problem, ctx: ScoreContext
) -> list[BoardPlan]:
    boards: list[BoardPlan] = []
    kerf = problem.kerf_mm
    for piece in pieces:
        best: tuple[float, int, int, str] | None = None  # (delta, prio, key, action)

        def consider(delta: float, prio: int, key: int, action: str) -> None:
            # Higher delta wins; on (near-)ties prefer lower prio:
            # extend open board (0) > open remnant (1) > fresh board (2).
            nonlocal best
            if (
                best is None
                or delta > best[0] + 1e-9
                or (abs(delta - best[0]) <= 1e-9 and prio < best[1])
            ):
                best = (delta, prio, key, action)

        for i, b in enumerate(boards):
            if b.fits(piece, kerf):
                before = ctx.board_contribution(b)
                b.pieces.append(piece)
                consider(ctx.board_contribution(b) - before, 0, i, "extend")
                b.pieces.pop()

        for length in sorted(pool):
            if pool[length] > 0 and piece <= length:
                delta = ctx.board_contribution(
                    BoardPlan(source_kind=REMNANT, source_length_mm=length, pieces=[piece])
                )
                consider(delta, 1, length, "remnant")

        consider(
            ctx.board_contribution(
                BoardPlan(source_kind=NEW, source_length_mm=problem.stock_length_mm, pieces=[piece])
            ),
            2,
            0,
            "new",
        )

        _, _, key, action = best
        if action == "extend":
            boards[key].pieces.append(piece)
        elif action == "remnant":
            pool[key] -= 1
            boards.append(BoardPlan(source_kind=REMNANT, source_length_mm=key, pieces=[piece]))
        else:
            boards.append(
                BoardPlan(source_kind=NEW, source_length_mm=problem.stock_length_mm, pieces=[piece])
            )
    return boards


def _local_search(
    boards: list[BoardPlan],
    pool: Counter,
    problem: Problem,
    ctx: ScoreContext,
    iterations: int,
) -> list[BoardPlan]:
    rng = random.Random(problem.seed)
    kerf = problem.kerf_mm

    def score() -> float:
        return ctx.score(boards)

    def drop_if_empty(idx: int) -> None:
        b = boards[idx]
        if not b.pieces:
            if b.source_kind == REMNANT:
                pool[b.source_length_mm] += 1
            boards.pop(idx)

    for _ in range(iterations):
        if not boards:
            break
        move = rng.choice(("relocate", "swap", "repack"))
        current = score()

        if move == "relocate":
            bi = rng.randrange(len(boards))
            src = boards[bi]
            if not src.pieces:
                continue
            pi = rng.randrange(len(src.pieces))
            piece = src.pieces[pi]
            src_before = ctx.board_contribution(src)
            src.pieces.pop(pi)
            # An emptied board effectively contributes 0: a remnant source
            # returns to the pool, a fresh board is simply not opened.
            src_after = ctx.board_contribution(src) if src.pieces else 0.0
            src_delta = src_after - src_before

            best: tuple[float, str, int] | None = None
            for j, b in enumerate(boards):
                if j != bi and b.fits(piece, kerf):
                    before = ctx.board_contribution(b)
                    b.pieces.append(piece)
                    d = ctx.board_contribution(b) - before
                    b.pieces.pop()
                    if best is None or d > best[0]:
                        best = (d, "board", j)
            for length in sorted(pool):
                if pool[length] > 0 and piece <= length:
                    d = ctx.board_contribution(
                        BoardPlan(source_kind=REMNANT, source_length_mm=length, pieces=[piece])
                    )
                    if best is None or d > best[0]:
                        best = (d, "remnant", length)
            d_new = ctx.board_contribution(
                BoardPlan(source_kind=NEW, source_length_mm=problem.stock_length_mm, pieces=[piece])
            )
            if best is None or d_new > best[0]:
                best = (d_new, "new", 0)

            if best is not None and src_delta + best[0] > 1e-9:
                kind, key = best[1], best[2]
                if kind == "board":
                    boards[key].pieces.append(piece)
                elif kind == "remnant":
                    pool[key] -= 1
                    boards.append(
                        BoardPlan(source_kind=REMNANT, source_length_mm=key, pieces=[piece])
                    )
                else:
                    boards.append(
                        BoardPlan(
                            source_kind=NEW,
                            source_length_mm=problem.stock_length_mm,
                            pieces=[piece],
                        )
                    )
                drop_if_empty(bi)
            else:
                src.pieces.insert(pi, piece)

        elif move == "swap":
            if len(boards) < 2:
                continue
            bi, bj = rng.sample(range(len(boards)), 2)
            a, b = boards[bi], boards[bj]
            if not a.pieces or not b.pieces:
                continue
            pi, pj = rng.randrange(len(a.pieces)), rng.randrange(len(b.pieces))
            a.pieces[pi], b.pieces[pj] = b.pieces[pj], a.pieces[pi]
            if a.is_feasible(kerf) and b.is_feasible(kerf) and score() > current + 1e-9:
                continue  # keep the swap
            a.pieces[pi], b.pieces[pj] = b.pieces[pj], a.pieces[pi]  # revert

        else:  # repack two boards
            if len(boards) < 2:
                continue
            bi, bj = rng.sample(range(len(boards)), 2)
            a, b = boards[bi], boards[bj]
            combined = a.pieces + b.pieces
            if not combined or len(combined) > _REPACK_MAX_PIECES:
                continue

            def eff(board: BoardPlan) -> float:
                # Empty board = dropped board: contributes nothing.
                return ctx.board_contribution(board) if board.pieces else 0.0

            base = eff(a) + eff(b)
            best_split: tuple[float, list[int], list[int]] | None = None
            indices = range(len(combined))
            for r in range(len(combined) + 1):
                for subset in combinations(indices, r):
                    chosen = set(subset)
                    pa = [combined[i] for i in indices if i in chosen]
                    pb = [combined[i] for i in indices if i not in chosen]
                    ta = BoardPlan(a.source_kind, a.source_length_mm, pa)
                    tb = BoardPlan(b.source_kind, b.source_length_mm, pb)
                    if not (ta.is_feasible(kerf) and tb.is_feasible(kerf)):
                        continue
                    val = eff(ta) + eff(tb)
                    if best_split is None or val > best_split[0]:
                        best_split = (val, pa, pb)
            if best_split is not None and best_split[0] > base + 1e-9:
                a.pieces, b.pieces = best_split[1], best_split[2]
                # A board emptied by repack goes away (higher index first).
                for idx in sorted((bi, bj), reverse=True):
                    drop_if_empty(idx)

    return boards
