"""Server-rendered UI (Bosnian). Classic POST -> redirect -> render flow;
all business logic stays in the services layer."""

from datetime import UTC
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
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
from app.optimizer import NEW, REMNANT, BoardPlan
from app.services import planning
from app.services.inventory import ConfirmError, confirm_plan
from app.services.workshop import get_workshop_settings, set_workshop_setting
from app.viz import board_svg

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


def fmt_mm(mm: int | None) -> str:
    if mm is None:
        return "–"
    return str(mm)


def parse_mm(text: str) -> int:
    """'4000', '4000.0', '4000,0' -> 4000 (mm)."""
    return round(float(text.strip().replace(",", ".")))


templates.env.filters["mm"] = fmt_mm


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("poruka", request.query_params.get("poruka"))
    ctx.setdefault("greska", request.query_params.get("greska"))
    return templates.TemplateResponse(request, name, ctx)


def _redirect(url: str, poruka: str | None = None, greska: str | None = None):
    if poruka:
        url += f"?poruka={quote(poruka)}"
    elif greska:
        url += f"?greska={quote(greska)}"
    return RedirectResponse(url, status_code=303)


# ---- dashboard ----

@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    materials = db.query(Material).filter(Material.active).order_by(Material.name).all()
    summary = []
    for m in materials:
        remnant_count, remnant_mm = (
            db.query(func.count(Remnant.id), func.coalesce(func.sum(Remnant.length_mm), 0))
            .filter(Remnant.material_id == m.id, Remnant.status == RemnantStatus.AVAILABLE)
            .one()
        )
        summary.append(
            {"material": m, "remnant_count": remnant_count, "remnant_mm": remnant_mm}
        )
    draft_orders = (
        db.query(Order)
        .filter(Order.status == OrderStatus.DRAFT)
        .order_by(Order.created_at.desc())
        .limit(10)
        .all()
    )
    return _render(request, "index.html", summary=summary, draft_orders=draft_orders)


# ---- orders ----

@router.get("/narudzbe/nova", response_class=HTMLResponse)
def new_order_form(request: Request, db: Session = Depends(get_db)):
    materials = db.query(Material).filter(Material.active).order_by(Material.name).all()
    if not materials:
        return _redirect("/zalihe", greska="Prvo dodajte materijal.")
    return _render(request, "nova.html", materials=materials)


@router.post("/narudzbe/nova")
async def create_order(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    material_id = int(form["material_id"])
    customer = (form.get("customer") or "").strip() or None
    lengths = form.getlist("length_mm")
    quantities = form.getlist("quantity")
    items: dict[int, int] = {}
    for length_text, qty_text in zip(lengths, quantities):
        if not length_text.strip():
            continue
        try:
            length_mm = parse_mm(length_text)
            qty = int(qty_text or "1")
        except ValueError:
            return _redirect("/narudzbe/nova", greska=f"Neispravna dužina: {length_text}")
        if length_mm <= 0 or qty <= 0:
            return _redirect("/narudzbe/nova", greska="Dužine i količine moraju biti pozitivne.")
        items[length_mm] = items.get(length_mm, 0) + qty
    if not items:
        return _redirect("/narudzbe/nova", greska="Unesite bar jedan komad.")

    material = db.get(Material, material_id)
    too_long = [l for l in items if l > material.stock_length_mm]
    if too_long:
        return _redirect(
            "/narudzbe/nova",
            greska=f"Komad {fmt_mm(too_long[0])} mm je duži od daske ({fmt_mm(material.stock_length_mm)} mm).",
        )

    order = Order(material_id=material_id, customer=customer)
    order.items = [OrderItem(length_mm=l, quantity=q) for l, q in sorted(items.items())]
    db.add(order)
    db.commit()
    plan = planning.compute_plan(db, order)
    db.commit()
    return _redirect(f"/planovi/{plan.id}")


# ---- plans ----

def _plan_context(db: Session, plan: Plan) -> dict:
    order = db.get(Order, plan.order_id)
    material = db.get(Material, order.material_id)
    boards = planning.boards_from_json(plan.plan_json)
    result = planning.validate_stored_plan(db, plan, boards)
    available = planning.available_remnant_counts(db, order.material_id)
    used = [b.source_length_mm for b in boards if b.source_kind == REMNANT]
    for length in used:
        if available[length] > 0:
            available[length] -= 1
    remnant_options = sorted(
        (length for length, count in available.items() if count > 0), reverse=True
    )
    new_boards = sum(1 for b in boards if b.source_kind == NEW)
    board_views = []
    for b in boards:
        leftover = b.leftover_mm(plan.kerf_mm)
        board_views.append(
            {
                "board": b,
                "svg": Markup(board_svg(b, plan.kerf_mm, plan.min_usable_mm)),
                "leftover": leftover,
                "leftover_kind": "none"
                if leftover == 0
                else ("remnant" if leftover >= plan.min_usable_mm else "scrap"),
            }
        )
    return {
        "plan": plan,
        "order": order,
        "material": material,
        "board_views": board_views,
        "result": result,
        "remnant_options": remnant_options,
        "board_shortage": max(0, new_boards - material.new_board_count),
        "editable": plan.status == PlanStatus.DRAFT,
    }


@router.get("/planovi/{plan_id}", response_class=HTMLResponse)
def plan_view(plan_id: int, request: Request, db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404)
    return _render(request, "plan.html", **_plan_context(db, plan))


@router.post("/planovi/{plan_id}/preracunaj")
def recompute(plan_id: int, strategy: str = Form("heuristic"), db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None or plan.status != PlanStatus.DRAFT:
        raise HTTPException(409)
    order = db.get(Order, plan.order_id)
    new_plan = planning.compute_plan(db, order, strategy=strategy)
    db.commit()
    poruka = (
        "Plan je ponovo izračunat."
        if new_plan.strategy != "exact"
        else "Plan je izračunat preciznim rješavačem."
    )
    return _redirect(f"/planovi/{new_plan.id}", poruka=poruka)


@router.post("/planovi/{plan_id}/premjesti")
def move_piece(
    plan_id: int,
    board_idx: int = Form(...),
    piece_idx: int = Form(...),
    target: str = Form(...),  # "board:<idx>" | "new" | "remnant:<length>"
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if plan is None or plan.status != PlanStatus.DRAFT:
        raise HTTPException(409)
    boards = planning.boards_from_json(plan.plan_json)
    try:
        piece = boards[board_idx].pieces.pop(piece_idx)
    except IndexError:
        raise HTTPException(400)
    order = db.get(Order, plan.order_id)
    material = db.get(Material, order.material_id)
    if target == "new":
        boards.append(BoardPlan(NEW, material.stock_length_mm, [piece]))
    elif target.startswith("remnant:"):
        boards.append(BoardPlan(REMNANT, int(target.split(":")[1]), [piece]))
    elif target.startswith("board:"):
        boards[int(target.split(":")[1])].pieces.append(piece)
    else:
        raise HTTPException(400)
    boards = [b for b in boards if b.pieces]
    result = planning.validate_stored_plan(db, plan, boards)
    plan.plan_json = planning.boards_to_json(boards)
    plan.score = result.score
    plan.strategy = "manual"
    db.commit()
    return _redirect(f"/planovi/{plan.id}")


@router.post("/planovi/{plan_id}/potvrdi")
def confirm(plan_id: int, db: Session = Depends(get_db)):
    try:
        confirm_plan(db, plan_id)
    except ConfirmError as e:
        return _redirect(f"/planovi/{plan_id}", greska="; ".join(e.errors))
    return _redirect(f"/planovi/{plan_id}", poruka="Plan potvrđen — zalihe su ažurirane.")


# ---- inventory ----

@router.get("/zalihe", response_class=HTMLResponse)
def inventory(request: Request, db: Session = Depends(get_db)):
    materials = db.query(Material).order_by(Material.name).all()
    data = []
    for m in materials:
        groups = (
            db.query(Remnant.length_mm, func.count(Remnant.id))
            .filter(Remnant.material_id == m.id, Remnant.status == RemnantStatus.AVAILABLE)
            .group_by(Remnant.length_mm)
            .order_by(Remnant.length_mm.desc())
            .all()
        )
        reserved = (
            db.query(ReservedLength)
            .filter(ReservedLength.material_id == m.id)
            .order_by(ReservedLength.length_mm)
            .all()
        )
        data.append({"material": m, "groups": groups, "reserved": reserved})
    return _render(request, "zalihe.html", data=data)


@router.post("/zalihe/materijal")
def add_material(
    name: str = Form(...),
    stock_length_mm: str = Form(...),
    new_board_count: int = Form(0),
    db: Session = Depends(get_db),
):
    try:
        stock = parse_mm(stock_length_mm)
    except ValueError:
        return _redirect("/zalihe", greska="Neispravna dužina daske.")
    if db.query(Material).filter(Material.name == name.strip()).first():
        return _redirect("/zalihe", greska="Materijal s tim nazivom već postoji.")
    db.add(Material(name=name.strip(), stock_length_mm=stock, new_board_count=new_board_count))
    db.commit()
    return _redirect("/zalihe", poruka="Materijal dodan.")


@router.post("/zalihe/{material_id}/broj-dasaka")
def set_board_count(material_id: int, count: int = Form(...), db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if m is None:
        raise HTTPException(404)
    m.new_board_count = max(0, count)
    db.commit()
    return _redirect("/zalihe", poruka=f"{m.name}: {m.new_board_count} novih dasaka.")


@router.post("/zalihe/{material_id}/ostatak")
def add_remnant(
    material_id: int,
    length_mm: str = Form(...),
    count: int = Form(1),
    action: str = Form("dodaj"),
    db: Session = Depends(get_db),
):
    try:
        length = parse_mm(length_mm)
    except ValueError:
        return _redirect("/zalihe", greska="Neispravna dužina.")
    if action == "dodaj":
        for _ in range(max(1, count)):
            db.add(Remnant(material_id=material_id, length_mm=length))
        db.commit()
        return _redirect("/zalihe", poruka=f"Dodano: {count} × {fmt_mm(length)} mm.")
    rows = (
        db.query(Remnant)
        .filter(
            Remnant.material_id == material_id,
            Remnant.length_mm == length,
            Remnant.status == RemnantStatus.AVAILABLE,
        )
        .order_by(Remnant.created_at, Remnant.id)
        .limit(max(1, count))
        .all()
    )
    if len(rows) < count:
        return _redirect("/zalihe", greska=f"Na zalihi je samo {len(rows)} kom.")
    from datetime import datetime

    for row in rows:
        row.status = RemnantStatus.CONSUMED
        row.consumed_at = datetime.now(UTC)
    db.commit()
    return _redirect("/zalihe", poruka=f"Uklonjeno: {count} × {fmt_mm(length)} mm.")


@router.post("/zalihe/{material_id}/rezervisana")
def add_reserved(material_id: int, length_mm: str = Form(...), db: Session = Depends(get_db)):
    try:
        length = parse_mm(length_mm)
    except ValueError:
        return _redirect("/zalihe", greska="Neispravna dužina.")
    exists = (
        db.query(ReservedLength)
        .filter(
            ReservedLength.material_id == material_id, ReservedLength.length_mm == length
        )
        .first()
    )
    if not exists:
        db.add(ReservedLength(material_id=material_id, length_mm=length, source="user"))
        db.commit()
    return _redirect("/zalihe", poruka=f"Rezervisana dužina {fmt_mm(length)} mm.")


@router.post("/zalihe/rezervisana/{reserved_id}/obrisi")
def delete_reserved(reserved_id: int, db: Session = Depends(get_db)):
    r = db.get(ReservedLength, reserved_id)
    if r:
        db.delete(r)
        db.commit()
    return _redirect("/zalihe", poruka="Rezervisana dužina obrisana.")


# ---- history & stats ----

@router.get("/historija", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)):
    orders = (
        db.query(Order)
        .filter(Order.status == OrderStatus.CONFIRMED)
        .order_by(Order.confirmed_at.desc())
        .limit(100)
        .all()
    )
    rows = []
    for o in orders:
        plan = (
            db.query(Plan)
            .filter(Plan.order_id == o.id, Plan.status == PlanStatus.CONFIRMED)
            .first()
        )
        scrap = (
            db.query(func.coalesce(func.sum(ScrapLog.length_mm), 0))
            .filter(ScrapLog.order_id == o.id)
            .scalar()
        )
        rows.append({"order": o, "plan": plan, "scrap": scrap})
    scrap_total, scrap_events = (
        db.query(func.coalesce(func.sum(ScrapLog.length_mm), 0), func.count(ScrapLog.id)).one()
    )
    return _render(
        request, "historija.html", rows=rows, scrap_total=scrap_total, scrap_events=scrap_events
    )


# ---- settings ----

_EDITABLE = [
    ("kerf_mm", "Širina reza — kerf (mm)"),
    ("min_usable_mm", "Najkraći upotrebljivi ostatak (mm)"),
    ("weight_scrap", "Kazna po mm otpada"),
    ("weight_new_board", "Trošak načete nove daske"),
    ("reserved_length_bonus", "Zaštita rezervisanih dužina"),
    ("reserved_match_tolerance_mm", "Tolerancija rezervisane dužine (mm)"),
]


@router.get("/postavke", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    ws = get_workshop_settings(db)
    values = [(key, label, getattr(ws, key)) for key, label in _EDITABLE]
    return _render(request, "postavke.html", values=values)


@router.post("/postavke")
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key, _label in _EDITABLE:
        if key in form:
            try:
                float(str(form[key]).replace(",", "."))
            except ValueError:
                return _redirect("/postavke", greska=f"Neispravna vrijednost za {key}.")
            set_workshop_setting(db, key, str(form[key]).replace(",", "."))
    db.commit()
    return _redirect("/postavke", poruka="Postavke sačuvane.")
