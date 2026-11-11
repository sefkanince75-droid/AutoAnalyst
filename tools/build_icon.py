"""Build the Windows icon from AutoAnalyst's existing connected-node mark."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "autoanalyst.ico"


def _point(value: float, scale: float) -> int:
    return round(value * scale)


def build_icon() -> Path:
    size = 256
    scale = size / 40
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (_point(1, scale), _point(1, scale), _point(39, scale), _point(39, scale)),
        radius=_point(11, scale),
        fill="#111C2E",
        outline="#304361",
        width=max(1, _point(1, scale)),
    )
    center = (_point(20, scale), _point(20, scale))
    endpoints = [(10, 12.5), (10, 27.5), (30, 12), (30, 28)]
    line_colors = ["#7C83FF", "#7C83FF", "#4DA3FF", "#4DA3FF"]
    for endpoint, color in zip(endpoints, line_colors, strict=True):
        draw.line(
            (center, (_point(endpoint[0], scale), _point(endpoint[1], scale))),
            fill=color,
            width=_point(2.2, scale),
        )
    nodes = [
        (10, 12.5, 2.4, "#59D5E8"),
        (10, 27.5, 2.4, "#9A7BFF"),
        (20, 20, 3, "#F3F6FC"),
        (30, 12, 2.4, "#7C83FF"),
        (30, 28, 2.4, "#4DA3FF"),
    ]
    for x, y, radius, color in nodes:
        draw.ellipse(
            (
                _point(x - radius, scale),
                _point(y - radius, scale),
                _point(x + radius, scale),
                _point(y + radius, scale),
            ),
            fill=color,
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        OUTPUT,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return OUTPUT


if __name__ == "__main__":
    print(build_icon())
