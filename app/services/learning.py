"""Demand-frequency learning.

After each confirmed order, recompute which lengths are ordered often
and mirror them into reserved_lengths with source="learned". User-marked
rows (source="user") are never touched; a learned row is never created
for a length the user already reserved.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import Order, OrderItem, OrderStatus, ReservedLength


def refresh_learned_lengths(db: Session, material_id: int) -> list[int]:
    """Sync learned reserved lengths for a material. Returns the lengths."""
    min_orders = env.learn_min_orders
    top_n = env.learn_top_n

    rows = (
        db.query(OrderItem.length_mm, func.count(func.distinct(Order.id)).label("n"))
        .join(Order, OrderItem.order_id == Order.id)
        .filter(Order.material_id == material_id, Order.status == OrderStatus.CONFIRMED)
        .group_by(OrderItem.length_mm)
        .having(func.count(func.distinct(Order.id)) >= min_orders)
        .order_by(func.count(func.distinct(Order.id)).desc(), OrderItem.length_mm.desc())
        .limit(top_n)
        .all()
    )
    frequent = [length for length, _n in rows]

    existing = (
        db.query(ReservedLength).filter(ReservedLength.material_id == material_id).all()
    )
    user_lengths = {r.length_mm for r in existing if r.source == "user"}
    learned = {r.length_mm: r for r in existing if r.source == "learned"}

    target = [l for l in frequent if l not in user_lengths]
    for length in target:
        if length not in learned:
            db.add(
                ReservedLength(
                    material_id=material_id, length_mm=length, weight=1.0, source="learned"
                )
            )
    for length, row in learned.items():
        if length not in target:
            db.delete(row)
    return target
