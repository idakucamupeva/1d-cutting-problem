"""Bridge between the DB and the pure optimizer.

Builds a Problem from an order + a live inventory snapshot, runs the
solver, and (de)serializes plans to/from the JSON stored on Plan rows.
"""

from collections import Counter

from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import Material, Order, Plan, PlanStatus, Remnant, RemnantStatus, ReservedLength
from app.optimizer import BoardPlan, CutPlan, Problem, ScoringConfig, solve
from app.optimizer import ReservedLength as OptReservedLength
from app.optimizer.validate import ValidationResult, validate_plan
from app.services.workshop import WorkshopSettings, get_workshop_settings


def available_remnant_counts(db: Session, material_id: int) -> Counter[int]:
    counts: Counter[int] = Counter()
    rows = (
        db.query(Remnant.length_mm)
        .filter(Remnant.material_id == material_id, Remnant.status == RemnantStatus.AVAILABLE)
        .all()
    )
    for (length,) in rows:
        counts[length] += 1
    return counts


def build_problem(db: Session, order: Order, ws: WorkshopSettings | None = None) -> Problem:
    ws = ws or get_workshop_settings(db)
    material = db.get(Material, order.material_id)
    reserved = tuple(
        OptReservedLength(length_mm=r.length_mm, weight=r.weight)
        for r in db.query(ReservedLength)
        .filter(ReservedLength.material_id == order.material_id)
        .order_by(ReservedLength.length_mm)
    )
    scoring = ScoringConfig(
        weight_scrap=ws.weight_scrap,
        weight_new_board=ws.weight_new_board,
        remnant_value_per_mm=ws.remnant_value_per_mm,
        value_convexity=ws.value_convexity,
        reserved_lengths=reserved,
        reserved_bonus=ws.reserved_length_bonus,
        reserved_tolerance_mm=ws.reserved_match_tolerance_mm,
    )
    return Problem(
        demand=tuple(sorted((i.length_mm, i.quantity) for i in order.items)),
        remnants=tuple(sorted(available_remnant_counts(db, order.material_id).items())),
        stock_length_mm=material.stock_length_mm,
        kerf_mm=ws.kerf_mm,
        min_usable_mm=ws.min_usable_mm,
        scoring=scoring,
        seed=env.optimizer_seed,
    )


def boards_to_json(boards: list[BoardPlan]) -> dict:
    return {
        "boards": [
            {
                "source_kind": b.source_kind,
                "source_length_mm": b.source_length_mm,
                "pieces": list(b.pieces),
            }
            for b in boards
        ]
    }


def boards_from_json(data: dict) -> list[BoardPlan]:
    return [
        BoardPlan(
            source_kind=b["source_kind"],
            source_length_mm=int(b["source_length_mm"]),
            pieces=[int(p) for p in b["pieces"]],
        )
        for b in data.get("boards", [])
    ]


def compute_plan(db: Session, order: Order, strategy: str = "heuristic") -> Plan:
    """Run the optimizer for an order and store the result as a draft plan.

    Any previous draft plans for the order are discarded. Draft plans
    reserve no inventory; availability is re-checked at confirmation.
    """
    ws = get_workshop_settings(db)
    problem = build_problem(db, order, ws)
    cut_plan: CutPlan = solve(
        problem,
        strategy=strategy,
        exact_max_pieces=env.exact_solver_max_pieces,
        exact_timeout_s=env.exact_solver_timeout_s,
    )

    for old in db.query(Plan).filter(Plan.order_id == order.id, Plan.status == PlanStatus.DRAFT):
        old.status = PlanStatus.DISCARDED

    plan = Plan(
        order_id=order.id,
        plan_json=boards_to_json(cut_plan.boards),
        score=cut_plan.score,
        strategy=cut_plan.breakdown.get("strategy_used", strategy),
        kerf_mm=problem.kerf_mm,
        min_usable_mm=problem.min_usable_mm,
    )
    db.add(plan)
    db.flush()
    return plan


def validate_stored_plan(db: Session, plan: Plan, boards: list[BoardPlan]) -> ValidationResult:
    """Validate boards against the plan's order demand and live inventory."""
    order = db.get(Order, plan.order_id)
    problem = build_problem(db, order)
    return validate_plan(boards, problem)
