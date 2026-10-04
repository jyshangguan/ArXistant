#!/usr/bin/env python3
"""Regenerate ArXistant's raster favicon assets in src/assets/.

The design is the app logo simplified for the Chrome tab: the arXiv-red
square (with a subtle corner radius) and one bold white "A" whose counter
and crossbar stay legible at 16 px. This script rasterises the SAME
geometry that is hand-written in src/assets/favicon.svg — keep the two in
sync (see the matching comment inside the SVG).

Outputs (next to this script):
  favicon.ico          multi-size ICO (16, 32, 48)
  apple-touch-icon.png 180 x 180 PNG for iOS home-screen icons

Run:  python3 src/assets/make_favicon.py
"""

import os

from PIL import Image, ImageDraw

# ---- Design constants (128 x 128 coordinate space, mirroring favicon.svg) ----
RED = (179, 27, 27, 255)      # #B31B1B — the app/extension brand red
WHITE = (255, 255, 255, 255)
CANVAS = 128
CORNER_RADIUS = 24            # subtle squircle, ~19% like the Android tile
# The "A": flat apex, legs ~20 deg from vertical, crossbar 62-77% down.
OUTLINE = [(55, 20), (73, 20), (105, 108), (89, 108), (82, 88),
           (46, 88), (39, 108), (23, 108)]
COUNTER = [(64, 39), (77, 75), (51, 75)]


def render(size, supersample=8):
    """Draw the icon at `size` px via supersampling for crisp edges."""
    big = size * supersample
    scale = big / CANVAS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, big - 1, big - 1],
                           radius=CORNER_RADIUS * scale, fill=RED)
    mask = Image.new("L", (big, big), 0)
    mask_draw = ImageDraw.Draw(mask)
    scaled = lambda pts: [(x * scale, y * scale) for x, y in pts]
    mask_draw.polygon(scaled(OUTLINE), fill=255)
    mask_draw.polygon(scaled(COUNTER), fill=0)
    img.paste(WHITE, (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def main():
    assets = os.path.dirname(os.path.abspath(__file__))
    ico = os.path.join(assets, "favicon.ico")
    touch = os.path.join(assets, "apple-touch-icon.png")
    render(48).save(ico, format="ICO",
                    sizes=[(16, 16), (32, 32), (48, 48)])
    render(180).save(touch, format="PNG")
    print("wrote", ico)
    print("wrote", touch)


if __name__ == "__main__":
    main()
