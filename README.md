# Daske — 1D cutting stock optimizer for a wood workshop

Optimizes cutting plans over a pool of inventory remnants + fresh boards,
accounting for saw kerf and — crucially — the *business value of remnants*,
not just raw waste: leftovers above a configurable threshold return to
inventory, long remnants are worth disproportionately more, and lengths that
are frequently ordered ("reserved lengths", marked by the user or learned
from order history) are protected from being broken up for poor fits.

All lengths are integer **millimeters** everywhere — storage, optimizer,
API, and UI (input and display) — so there is never a unit conversion to
get wrong.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,exact]"   # drop `exact` to skip OR-Tools
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed
.venv/bin/uvicorn app.main:app --reload
```

UI at http://localhost:8000 (Bosnian), JSON API under `/api` (docs at `/docs`).

```bash
.venv/bin/pytest          # full suite
.venv/bin/ruff check app/ tests/ scripts/
```

## Architecture

| Layer | Where | Notes |
|---|---|---|
| Optimizer (pure) | `app/optimizer/` | No web/DB imports. Deterministic per (input, seed). |
| Services | `app/services/` | DB↔optimizer bridge, atomic confirm, learning. |
| API | `app/routers/api.py` | JSON surface, used by integration tests. |
| UI | `app/routers/ui.py` + `templates/` | Server-rendered, form POSTs, SVG cut bars. |
| DB | `app/models.py`, `alembic/` | SQLite; Postgres = change `DASKE_DATABASE_URL`. |

### Optimizer

- **Geometry** (`types.py`): pieces `p1..pn` from stock `L` are feasible iff
  `sum(p) + (n-1)*kerf <= L`; leftover is `max(0, L - sum(p) - n*kerf)`
  (the trailing cut is skipped on an exact fit).
- **Scoring** (`scoring.py`): plan score =
  `Σ worth(leftover>=min_usable) − Σ scrap·W_scrap − Σ worth(consumed remnants)
  − new_boards·(value(stock) + W_new_board)`, with a convex, pluggable value
  function and a reserved-length bonus. See the module docstring for why the
  handling cost `W_new_board` exists and how `reserved_bonus` must relate to it.
- **Heuristic** (`heuristic.py`): Best-Fit-Decreasing ranked by *marginal
  score delta*, then seeded local search (relocate / swap / two-board repack).
- **Exact** (`exact.py`, optional): CP-SAT assignment model with the leftover
  worth linearized as a 10 mm lookup table; re-scored with the true scoring
  function and returned only if it beats the heuristic. Falls back to the
  heuristic when OR-Tools is missing, the instance exceeds
  `exact_solver_max_pieces`, or the (deterministic-time) limit passes.
- **Validation** (`validate.py`): shared by optimizer output, manual plan
  edits, and re-checked inside the confirm transaction.

### Lifecycle

Order → draft plan (reserves nothing) → optional manual edits (each edit
re-validated) → **confirm**: one transaction that re-validates against live
inventory, consumes remnants FIFO per length, decrements board stock, inserts
created remnants, logs scrap, archives the order, and refreshes learned
frequent lengths. Any failure rolls back everything.

### Configuration

Environment (prefix `DASKE_`, see `app/config.py`) seeds the DB `settings`
table; kerf, min-usable threshold and scoring weights are editable at runtime
under **Postavke**. Stock length is per material.
