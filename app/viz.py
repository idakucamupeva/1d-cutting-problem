"""Server-side SVG rendering of cutting plans.

One horizontal bar per board: pieces, kerf gaps, and the leftover
(green = usable remnant, red = scrap). Pure function of plan data, so it
also works in printouts and the read-only history view.
"""

from app.optimizer.types import BoardPlan

_W = 1000  # viewBox width
_H = 64


def _fmt_mm(mm: int) -> str:
    return str(mm)


def board_svg(board: BoardPlan, kerf_mm: int, min_usable_mm: int) -> str:
    total = board.source_length_mm
    scale = _W / total
    y, h = 8, 40
    parts: list[str] = [
        f'<svg viewBox="0 0 {_W} {_H}" class="board-svg" role="img" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="{y}" width="{_W}" height="{h}" rx="4" class="svg-source"/>',
    ]
    x = 0.0
    n = len(board.pieces)
    for i, piece in enumerate(board.pieces):
        w = piece * scale
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" class="svg-piece"/>'
        )
        if w > 55:  # only label pieces wide enough to fit text
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + 6}" text-anchor="middle" '
                f'class="svg-label">{_fmt_mm(piece)}</text>'
            )
        x += w
        # kerf gap after every piece except a final piece flush with the end
        is_last = i == n - 1
        rest = total - sum(board.pieces) - (n - 1) * kerf_mm
        if not (is_last and rest == 0):
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{max(kerf_mm * scale, 1.5):.1f}" '
                f'height="{h}" class="svg-kerf"/>'
            )
            x += kerf_mm * scale
    leftover = board.leftover_mm(kerf_mm)
    if leftover > 0:
        w = leftover * scale
        cls = "svg-remnant" if leftover >= min_usable_mm else "svg-scrap"
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" class="{cls}"/>')
        if w > 55:
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + 6}" text-anchor="middle" '
                f'class="svg-label">{_fmt_mm(leftover)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)
