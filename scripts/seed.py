"""Seed the DB with workshop defaults. Idempotent — safe to re-run.

Usage: .venv/bin/python -m scripts.seed
"""

from app.config import settings
from app.db import SessionLocal
from app.models import Material, Setting

DEFAULT_SETTINGS = {
    "kerf_mm": str(settings.default_kerf_mm),
    "min_usable_mm": str(settings.default_min_usable_mm),
    "weight_scrap": str(settings.weight_scrap),
    "weight_new_board": str(settings.weight_new_board),
    "remnant_value_per_mm": str(settings.remnant_value_per_mm),
    "value_convexity": str(settings.value_convexity),
    "reserved_length_bonus": str(settings.reserved_length_bonus),
    "reserved_match_tolerance_mm": str(settings.reserved_match_tolerance_mm),
}


def seed() -> None:
    with SessionLocal() as db:
        for key, value in DEFAULT_SETTINGS.items():
            if db.get(Setting, key) is None:
                db.add(Setting(key=key, value=value))

        if db.query(Material).count() == 0:
            db.add(
                Material(
                    name="Standardna daska",
                    stock_length_mm=settings.default_stock_length_mm,
                    new_board_count=0,
                )
            )
        db.commit()


if __name__ == "__main__":
    seed()
    print("Seeded.")
