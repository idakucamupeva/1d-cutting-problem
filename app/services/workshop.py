"""Workshop-level runtime settings, loaded from the DB settings table."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import Setting


@dataclass(frozen=True)
class WorkshopSettings:
    kerf_mm: int
    min_usable_mm: int
    weight_scrap: float
    weight_new_board: float
    remnant_value_per_mm: float
    value_convexity: float
    reserved_length_bonus: float
    reserved_match_tolerance_mm: int


_DEFAULTS = {
    "kerf_mm": env.default_kerf_mm,
    "min_usable_mm": env.default_min_usable_mm,
    "weight_scrap": env.weight_scrap,
    "weight_new_board": env.weight_new_board,
    "remnant_value_per_mm": env.remnant_value_per_mm,
    "value_convexity": env.value_convexity,
    "reserved_length_bonus": env.reserved_length_bonus,
    "reserved_match_tolerance_mm": env.reserved_match_tolerance_mm,
}

_INT_KEYS = {"kerf_mm", "min_usable_mm", "reserved_match_tolerance_mm"}


def get_workshop_settings(db: Session) -> WorkshopSettings:
    stored = {s.key: s.value for s in db.query(Setting).all()}
    values = {}
    for key, default in _DEFAULTS.items():
        raw = stored.get(key)
        if raw is None:
            values[key] = default
        else:
            values[key] = int(float(raw)) if key in _INT_KEYS else float(raw)
    return WorkshopSettings(**values)


def set_workshop_setting(db: Session, key: str, value: str) -> None:
    if key not in _DEFAULTS:
        raise ValueError(f"Unknown setting: {key}")
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
