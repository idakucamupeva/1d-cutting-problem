"""Application configuration.

All lengths are integer millimeters. These are *defaults*; per-material
values (stock length) and workshop-level values (kerf, min_usable) are
editable at runtime and stored in the DB settings table — config values
seed the DB on first run.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASKE_", env_file=".env")

    database_url: str = "sqlite:///./daske.db"

    # Seeded workshop defaults (mm)
    default_stock_length_mm: int = 13_000
    default_kerf_mm: int = 4
    default_min_usable_mm: int = 2_000

    # Scoring weights (see optimizer/scoring.py for the model)
    weight_scrap: float = 1.0           # penalty per mm of scrap
    weight_new_board: float = 2000.0    # handling cost per fresh board, ON TOP of its material value
    remnant_value_per_mm: float = 0.6   # base slope of remnant value
    value_convexity: float = 0.5        # long remnants disproportionately valuable
    reserved_length_bonus: float = 3000.0
    reserved_match_tolerance_mm: int = 50

    # Optimizer limits
    local_search_iterations: int = 2_000
    exact_solver_max_pieces: int = 60
    exact_solver_timeout_s: float = 10.0

    optimizer_seed: int = 42

    # Demand-frequency learning: a length ordered in >= learn_min_orders
    # confirmed orders becomes a learned reserved length (top learn_top_n).
    learn_min_orders: int = 3
    learn_top_n: int = 5


settings = Settings()
