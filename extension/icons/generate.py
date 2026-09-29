"""Render the extension's toolbar icons.

Committed so the icons are reproducible rather than binary blobs nobody can
edit: change a constant here and re-run, instead of hunting for whatever tool
made the PNG.

    pip install Pillow && python extension/icons/generate.py

Pillow is not a project dependency: this runs when the icon changes, which is
approximately never, and nothing at runtime or in the tests imports it.

Design notes, since the constraint is unusual. The icon is read at 16px in a
browser toolbar, so it carries one shape and no detail — a star, the same mark
the extension injects on LinkedIn and Indeed job cards, so the thing you click
on a card and the thing in your toolbar are recognisably one product. The star
is deliberately fatter than a classic five-point (inner radius 0.52 of the
outer rather than 0.382); thin points disappear into the background at 16px.

The tile is solid rather than transparent so the mark holds up against both a
light and a dark toolbar, and everything is drawn at 16x and downsampled, since
Pillow's polygon fill has no anti-aliasing of its own.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 32, 48, 128)
SUPERSAMPLE = 16

BLUE = (10, 102, 194, 255)  # #0a66c2 — the same blue as the in-page Save button
WHITE = (255, 255, 255, 255)

CORNER = 0.22  # tile corner radius, as a fraction of the side
STAR_RADIUS = 0.37  # outer radius, as a fraction of the side
STAR_INNER = 0.52  # inner radius, as a fraction of the outer
STAR_RISE = 0.02  # lift the star slightly: a star's mass sits low


def star_points(cx: float, cy: float, outer: float, inner: float, points: int = 5):
    """Vertices of a star, first point straight up."""
    vertices = []
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / points
        vertices.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return vertices


def render(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=CORNER * big, fill=BLUE)

    outer = STAR_RADIUS * big
    draw.polygon(
        star_points(big / 2, big / 2 - STAR_RISE * big, outer, outer * STAR_INNER),
        fill=WHITE,
    )

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    here = Path(__file__).resolve().parent
    for size in SIZES:
        path = here / f"icon{size}.png"
        render(size).save(path, optimize=True)
        print(f"wrote {path.relative_to(here.parent.parent)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
