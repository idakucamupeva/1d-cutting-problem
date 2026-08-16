"""Pydantic request/response models for the API."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---- materials ----

class MaterialIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    stock_length_mm: int = Field(gt=0)
    new_board_count: int = Field(default=0, ge=0)


class MaterialPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    stock_length_mm: int | None = Field(default=None, gt=0)
    new_board_count: int | None = Field(default=None, ge=0)
    active: bool | None = None


class MaterialOut(BaseModel):
    id: int
    name: str
    stock_length_mm: int
    new_board_count: int
    active: bool

    model_config = {"from_attributes": True}


# ---- orders ----

class OrderItemIn(BaseModel):
    length_mm: int = Field(gt=0)
    quantity: int = Field(gt=0)


class OrderIn(BaseModel):
    material_id: int
    customer: str | None = Field(default=None, max_length=200)
    items: list[OrderItemIn] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def unique_lengths(cls, items: list[OrderItemIn]) -> list[OrderItemIn]:
        lengths = [i.length_mm for i in items]
        if len(lengths) != len(set(lengths)):
            raise ValueError("duplicate lengths; merge quantities instead")
        return items


class OrderItemOut(BaseModel):
    length_mm: int
    quantity: int

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: int
    material_id: int
    customer: str | None
    status: str
    created_at: datetime
    confirmed_at: datetime | None
    items: list[OrderItemOut]

    model_config = {"from_attributes": True}

    @field_validator("status", mode="before")
    @classmethod
    def enum_value(cls, v):
        return getattr(v, "value", v)


# ---- plans ----

class BoardIn(BaseModel):
    source_kind: str = Field(pattern="^(new|remnant)$")
    source_length_mm: int = Field(gt=0)
    pieces: list[int] = Field(min_length=1)


class PlanEdit(BaseModel):
    boards: list[BoardIn]


class BoardOut(BaseModel):
    source_kind: str
    source_length_mm: int
    pieces: list[int]
    leftover_mm: int
    leftover_kind: str  # "remnant" | "scrap" | "none"
    cuts: int


class ValidationOut(BaseModel):
    ok: bool
    board_errors: list[tuple[int, str]]
    demand_errors: list[str]
    inventory_errors: list[str]
    score: float
    breakdown: dict


class PlanOut(BaseModel):
    id: int
    order_id: int
    status: str
    strategy: str
    kerf_mm: int
    min_usable_mm: int
    score: float | None
    boards: list[BoardOut]
    breakdown: dict
    board_shortage: int  # new boards needed beyond current stock

    @field_validator("status", mode="before")
    @classmethod
    def enum_value(cls, v):
        return getattr(v, "value", v)


# ---- inventory ----

class RemnantGroupOut(BaseModel):
    length_mm: int
    count: int
    oldest_created_at: datetime


class RemnantAdd(BaseModel):
    material_id: int
    length_mm: int = Field(gt=0)
    count: int = Field(default=1, gt=0, le=1000)


class RemnantRemove(BaseModel):
    material_id: int
    length_mm: int = Field(gt=0)
    count: int = Field(default=1, gt=0)


# ---- reserved lengths ----

class ReservedLengthIn(BaseModel):
    material_id: int
    length_mm: int = Field(gt=0)
    weight: float = Field(default=1.0, gt=0)


class ReservedLengthOut(BaseModel):
    id: int
    material_id: int
    length_mm: int
    weight: float
    source: str

    model_config = {"from_attributes": True}
