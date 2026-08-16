"""JSON API. The htmx UI (Phase 3) calls the same services; this API is
the programmatic surface and what integration tests exercise."""

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Material,
    Order,
    OrderItem,
    OrderStatus,
    Plan,
    PlanStatus,
    Remnant,
    RemnantStatus,
    ReservedLength,
    ScrapLog,
)
from app.optimizer import NEW, BoardPlan
from app.schemas import (
    BoardOut,
    MaterialIn,
    MaterialOut,
    MaterialPatch,
    OrderIn,
    OrderOut,
    PlanEdit,
    PlanOut,
    RemnantAdd,
    RemnantGroupOut,
    RemnantRemove,
    ReservedLengthIn,
    ReservedLengthOut,
    ValidationOut,
)
from app.services import planning
from app.services.inventory import ConfirmError, confirm_plan

router = APIRouter(prefix="/api")


# ---- materials ----

@router.get("/materials", response_model=list[MaterialOut])
def list_materials(db: Session = Depends(get_db)):
    return db.query(Material).order_by(Material.name).all()


@router.post("/materials", response_model=MaterialOut, status_code=201)
def create_material(data: MaterialIn, db: Session = Depends(get_db)):
    if db.query(Material).filter(Material.name == data.name).first():
        raise HTTPException(409, "material with this name already exists")
    m = Material(**data.model_dump())
    db.add(m)
    db.commit()
    return m


@router.patch("/materials/{material_id}", response_model=MaterialOut)
def update_material(material_id: int, data: MaterialPatch, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if m is None:
        raise HTTPException(404, "material not found")
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(m, key, value)
    db.commit()
    return m


# ---- orders ----

@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(data: OrderIn, db: Session = Depends(get_db)):
    if db.get(Material, data.material_id) is None:
        raise HTTPException(404, "material not found")
    order = Order(material_id=data.material_id, customer=data.customer)
    order.items = [
        OrderItem(length_mm=i.length_mm, quantity=i.quantity) for i in data.items
    ]
    db.add(order)
    db.commit()
    return order


@router.get("/orders", response_model=list[OrderOut])
def list_orders(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Order).order_by(Order.created_at.desc())
    if status:
        q = q.filter(Order.status == OrderStatus(status))
    return q.limit(200).all()


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    return order


# ---- plans ----

def _board_shortage(db: Session, plan: Plan, boards: list[BoardPlan]) -> int:
    order = db.get(Order, plan.order_id)
    material = db.get(Material, order.material_id)
    new_boards = sum(1 for b in boards if b.source_kind == NEW)
    return max(0, new_boards - material.new_board_count)


def _plan_out(db: Session, plan: Plan) -> PlanOut:
    boards = planning.boards_from_json(plan.plan_json)
    result = planning.validate_stored_plan(db, plan, boards)
    outs = []
    for b in boards:
        leftover = b.leftover_mm(plan.kerf_mm)
        kind = "none" if leftover == 0 else (
            "remnant" if leftover >= plan.min_usable_mm else "scrap"
        )
        outs.append(
            BoardOut(
                source_kind=b.source_kind,
                source_length_mm=b.source_length_mm,
                pieces=b.pieces,
                leftover_mm=leftover,
                leftover_kind=kind,
                cuts=b.cuts_count(plan.kerf_mm),
            )
        )
    return PlanOut(
        id=plan.id,
        order_id=plan.order_id,
        status=plan.status,
        strategy=plan.strategy,
        kerf_mm=plan.kerf_mm,
        min_usable_mm=plan.min_usable_mm,
        score=plan.score,
        boards=outs,
        breakdown=result.breakdown,
        board_shortage=_board_shortage(db, plan, boards),
    )


@router.post("/orders/{order_id}/plan", response_model=PlanOut, status_code=201)
def compute_plan(order_id: int, strategy: str = "heuristic", db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status != OrderStatus.DRAFT:
        raise HTTPException(409, f"order is {order.status.value}")
    plan = planning.compute_plan(db, order, strategy=strategy)
    db.commit()
    return _plan_out(db, plan)


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404, "plan not found")
    return _plan_out(db, plan)


@router.put("/plans/{plan_id}", response_model=ValidationOut)
def edit_plan(plan_id: int, data: PlanEdit, db: Session = Depends(get_db)):
    """Save a manually edited draft plan. Always returns the validation
    result; the edit is saved even if invalid (so the user can keep
    fixing it), but an invalid plan can never be confirmed."""
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404, "plan not found")
    if plan.status != PlanStatus.DRAFT:
        raise HTTPException(409, "only draft plans can be edited")
    boards = [
        BoardPlan(b.source_kind, b.source_length_mm, list(b.pieces)) for b in data.boards
    ]
    result = planning.validate_stored_plan(db, plan, boards)
    plan.plan_json = planning.boards_to_json(boards)
    plan.score = result.score
    plan.strategy = "manual"
    db.commit()
    return ValidationOut(
        ok=result.ok,
        board_errors=result.board_errors,
        demand_errors=result.demand_errors,
        inventory_errors=result.inventory_errors,
        score=result.score,
        breakdown=result.breakdown,
    )


@router.post("/plans/{plan_id}/confirm")
def confirm(plan_id: int, db: Session = Depends(get_db)):
    try:
        return confirm_plan(db, plan_id)
    except ConfirmError as e:
        raise HTTPException(409, detail=e.errors) from e


# ---- inventory ----

@router.get("/inventory/{material_id}/remnants", response_model=list[RemnantGroupOut])
def list_remnants(material_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(
            Remnant.length_mm,
            func.count(Remnant.id),
            func.min(Remnant.created_at),
        )
        .filter(Remnant.material_id == material_id, Remnant.status == RemnantStatus.AVAILABLE)
        .group_by(Remnant.length_mm)
        .order_by(Remnant.length_mm.desc())
        .all()
    )
    return [
        RemnantGroupOut(length_mm=length, count=count, oldest_created_at=oldest)
        for length, count, oldest in rows
    ]


@router.post("/inventory/remnants", status_code=201)
def add_remnants(data: RemnantAdd, db: Session = Depends(get_db)):
    if db.get(Material, data.material_id) is None:
        raise HTTPException(404, "material not found")
    for _ in range(data.count):
        db.add(Remnant(material_id=data.material_id, length_mm=data.length_mm))
    db.commit()
    return {"added": data.count}


@router.post("/inventory/remnants/remove")
def remove_remnants(data: RemnantRemove, db: Session = Depends(get_db)):
    """Manual correction: remove remnants (damaged, miscounted...), oldest first."""
    rows = (
        db.query(Remnant)
        .filter(
            Remnant.material_id == data.material_id,
            Remnant.length_mm == data.length_mm,
            Remnant.status == RemnantStatus.AVAILABLE,
        )
        .order_by(Remnant.created_at, Remnant.id)
        .limit(data.count)
        .all()
    )
    if len(rows) < data.count:
        raise HTTPException(409, f"only {len(rows)} available")
    now = datetime.now(UTC)
    for row in rows:
        row.status = RemnantStatus.CONSUMED
        row.consumed_at = now
    db.commit()
    return {"removed": len(rows)}


# ---- reserved lengths ----

@router.get("/reserved-lengths/{material_id}", response_model=list[ReservedLengthOut])
def list_reserved(material_id: int, db: Session = Depends(get_db)):
    return (
        db.query(ReservedLength)
        .filter(ReservedLength.material_id == material_id)
        .order_by(ReservedLength.length_mm)
        .all()
    )


@router.post("/reserved-lengths", response_model=ReservedLengthOut, status_code=201)
def add_reserved(data: ReservedLengthIn, db: Session = Depends(get_db)):
    existing = (
        db.query(ReservedLength)
        .filter(
            ReservedLength.material_id == data.material_id,
            ReservedLength.length_mm == data.length_mm,
        )
        .first()
    )
    if existing:
        existing.weight = data.weight
        db.commit()
        return existing
    r = ReservedLength(**data.model_dump(), source="user")
    db.add(r)
    db.commit()
    return r


@router.delete("/reserved-lengths/{reserved_id}", status_code=204)
def delete_reserved(reserved_id: int, db: Session = Depends(get_db)):
    r = db.get(ReservedLength, reserved_id)
    if r:
        db.delete(r)
        db.commit()


# ---- stats ----

@router.get("/stats/{material_id}")
def stats(material_id: int, db: Session = Depends(get_db)):
    scrap_total, scrap_events = (
        db.query(func.coalesce(func.sum(ScrapLog.length_mm), 0), func.count(ScrapLog.id))
        .filter(ScrapLog.material_id == material_id)
        .one()
    )
    remnant_rows = (
        db.query(Remnant.length_mm)
        .filter(Remnant.material_id == material_id, Remnant.status == RemnantStatus.AVAILABLE)
        .all()
    )
    lengths = Counter(length for (length,) in remnant_rows)
    orders_confirmed = (
        db.query(func.count(Order.id))
        .filter(Order.material_id == material_id, Order.status == OrderStatus.CONFIRMED)
        .scalar()
    )
    return {
        "scrap_total_mm": scrap_total,
        "scrap_events": scrap_events,
        "remnant_count": sum(lengths.values()),
        "remnant_total_mm": sum(l * c for l, c in lengths.items()),
        "orders_confirmed": orders_confirmed,
    }
