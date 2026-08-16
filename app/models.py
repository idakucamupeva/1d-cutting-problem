"""ORM models. All lengths are integer millimeters.

Remnants are stored as individual rows (audit: source, created_at) but the
UI and the optimizer group them by (material, length); confirmation
consumes the oldest available row of a given length (FIFO).
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Material(Base):
    """A board type/profile. Plans never mix materials."""

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    stock_length_mm: Mapped[int] = mapped_column(Integer)
    new_board_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)

    remnants: Mapped[list["Remnant"]] = relationship(back_populates="material")


class RemnantStatus(enum.Enum):
    AVAILABLE = "available"
    CONSUMED = "consumed"


class Remnant(Base):
    __tablename__ = "remnants"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    length_mm: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[RemnantStatus] = mapped_column(
        Enum(RemnantStatus), default=RemnantStatus.AVAILABLE, index=True
    )
    # Provenance
    source_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Consumption
    consumed_by_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    material: Mapped[Material] = relationship(back_populates="remnants")


class OrderStatus(enum.Enum):
    DRAFT = "draft"        # entered, plan not confirmed
    CONFIRMED = "confirmed"  # plan confirmed, inventory updated


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    customer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    plans: Mapped[list["Plan"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "length_mm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    length_mm: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class PlanStatus(enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class Plan(Base):
    """A cutting plan for an order.

    `plan_json` is the serialized optimizer output (see optimizer/types.py):
    a list of board entries, each with source kind (new board or remnant
    length), the ordered pieces to cut, and the resulting leftover with its
    classification (usable remnant vs scrap). Draft plans reserve nothing;
    confirmation re-validates availability inside the transaction.
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[PlanStatus] = mapped_column(Enum(PlanStatus), default=PlanStatus.DRAFT)
    plan_json: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float | None] = mapped_column(nullable=True)
    strategy: Mapped[str] = mapped_column(String(30), default="heuristic")
    kerf_mm: Mapped[int] = mapped_column(Integer)
    min_usable_mm: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped[Order] = relationship(back_populates="plans")


class ScrapLog(Base):
    __tablename__ = "scrap_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    length_mm: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReservedLength(Base):
    """Lengths the optimizer should be reluctant to break remnants of.

    `source` distinguishes user-marked lengths from ones learned from
    order history (Phase 4).
    """

    __tablename__ = "reserved_lengths"
    __table_args__ = (UniqueConstraint("material_id", "length_mm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    length_mm: Mapped[int] = mapped_column(Integer)
    weight: Mapped[float] = mapped_column(default=1.0)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user | learned


class Setting(Base):
    """Workshop-level runtime settings (kerf, min_usable, scoring weights)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
