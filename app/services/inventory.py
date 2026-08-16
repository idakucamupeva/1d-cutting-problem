"""Atomic plan confirmation.

Everything happens in ONE transaction on the caller's session:
  1. re-validate the plan against a live inventory snapshot
  2. consume remnant sources (oldest AVAILABLE row per length, FIFO)
  3. decrement the material's new-board count
  4. insert created remnants, log scrap
  5. mark plan + order confirmed
Any failure raises ConfirmError and the session is rolled back -- the
caller commits only on success.

Remnant rows are locked with SELECT ... FOR UPDATE (no-op on SQLite,
which serializes writers anyway; real locking on Postgres).
"""

import dataclasses
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    Material,
    Order,
    OrderStatus,
    Plan,
    PlanStatus,
    Remnant,
    RemnantStatus,
    ScrapLog,
)
from app.optimizer import REMNANT
from app.optimizer.validate import validate_plan
from app.services.planning import boards_from_json, build_problem


class ConfirmError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def confirm_plan(db: Session, plan_id: int) -> dict:
    """Confirm a draft plan and apply it to inventory. Returns a summary.

    Raises ConfirmError (after rolling back) on any validation failure.
    """
    try:
        return _confirm(db, plan_id)
    except Exception:
        db.rollback()
        raise


def _confirm(db: Session, plan_id: int) -> dict:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise ConfirmError(["plan not found"])
    if plan.status != PlanStatus.DRAFT:
        raise ConfirmError([f"plan is {plan.status.value}, only drafts can be confirmed"])
    order = db.get(Order, plan.order_id)
    if order.status != OrderStatus.DRAFT:
        raise ConfirmError([f"order is {order.status.value}"])

    boards = boards_from_json(plan.plan_json)

    # Validate against a LIVE snapshot, but with the geometry the plan was
    # built with (kerf/min_usable may have been edited since).
    problem = dataclasses.replace(
        build_problem(db, order),
        kerf_mm=plan.kerf_mm,
        min_usable_mm=plan.min_usable_mm,
    )
    result = validate_plan(boards, problem)
    if not result.ok:
        errors = (
            [f"board {i + 1}: {msg}" for i, msg in result.board_errors]
            + result.demand_errors
            + result.inventory_errors
        )
        raise ConfirmError(errors)

    now = datetime.now(UTC)

    # 2. Consume remnant sources, oldest first (FIFO per length).
    consumed_ids: list[int] = []
    for board in boards:
        if board.source_kind != REMNANT:
            continue
        query = db.query(Remnant).filter(
            Remnant.material_id == order.material_id,
            Remnant.length_mm == board.source_length_mm,
            Remnant.status == RemnantStatus.AVAILABLE,
        )
        if consumed_ids:
            # autoflush is off: earlier consumptions in this loop are not
            # yet visible to SQL, so exclude them explicitly.
            query = query.filter(Remnant.id.notin_(consumed_ids))
        row = query.order_by(Remnant.created_at, Remnant.id).with_for_update().first()
        if row is None:  # validated above, but re-check under lock
            raise ConfirmError(
                [f"remnant {board.source_length_mm} mm no longer available"]
            )
        row.status = RemnantStatus.CONSUMED
        row.consumed_by_order_id = order.id
        row.consumed_at = now
        consumed_ids.append(row.id)

    # 3. New boards.
    material = db.get(Material, order.material_id)
    new_boards = sum(1 for b in boards if b.source_kind != REMNANT)
    board_shortage = max(0, new_boards - material.new_board_count)
    material.new_board_count = max(0, material.new_board_count - new_boards)

    # 4. Created remnants + scrap.
    created: list[int] = []
    scrap_total = 0
    for board in boards:
        leftover = board.leftover_mm(plan.kerf_mm)
        if leftover >= plan.min_usable_mm:
            db.add(
                Remnant(
                    material_id=order.material_id,
                    length_mm=leftover,
                    source_order_id=order.id,
                    created_at=now,
                )
            )
            created.append(leftover)
        elif leftover > 0:
            db.add(
                ScrapLog(
                    material_id=order.material_id,
                    order_id=order.id,
                    length_mm=leftover,
                    created_at=now,
                )
            )
            scrap_total += leftover

    # 5. Archive.
    plan.status = PlanStatus.CONFIRMED
    plan.confirmed_at = now
    order.status = OrderStatus.CONFIRMED
    order.confirmed_at = now

    # 6. Refresh learned frequent lengths (same transaction; flush so the
    # aggregate query sees this order as confirmed).
    db.flush()
    from app.services.learning import refresh_learned_lengths

    refresh_learned_lengths(db, order.material_id)

    db.commit()
    return {
        "plan_id": plan.id,
        "order_id": order.id,
        "remnants_consumed": len(consumed_ids),
        "new_boards_used": new_boards,
        "board_shortage": board_shortage,  # >0 => stock count hit zero; owner must buy
        "remnants_created": sorted(created),
        "scrap_mm": scrap_total,
    }
