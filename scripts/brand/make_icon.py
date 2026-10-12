#!/usr/bin/env python3
"""Generate the WagWatch app icon from one design: the heartbeat W.

The W is a pulse trace (a heartbeat, a bark waveform, and the W of WagWatch). Its centre peak ends in an amber
bead: the treat, and the watching eye. Colours come from the dashboard theme (frontend/app/globals.css).

Writes:
  docs/brand/wagwatch-icon.svg                     master (1024 viewBox)
  docs/brand/wagwatch-icon-512.png                 store/preview size
  android/app/src/main/res/mipmap-*/ic_launcher*.png   legacy launcher (pre-API 26)
  android/app/src/main/res/drawable/ic_launcher_foreground.xml   adaptive foreground (vector)
  android/app/src/main/res/values/ic_launcher_colors.xml         adaptive background colour
  android/app/src/main/res/mipmap-anydpi-v26/ic_launcher*.xml    adaptive icon entries
  frontend/app/icon.svg                            Next.js app icon (served as the favicon)
  frontend/app/favicon.ico                         multi-size fallback

Run from the repo root:  python3 scripts/brand/make_icon.py   (needs Pillow)
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[2]
BG = (15, 17, 19)        # --background of the dark theme
TEAL = (63, 184, 193)    # --accent (dark theme)
AMBER = (245, 196, 81)   # the treat / the eye
WHITE = (255, 255, 255)

# An ECG strip that spells W, on a 1024 grid: flat lead-in and tail on the baseline (y=512), then the W
# strokes. The centre peak is the amber bead.
W_PTS = [(110, 512), (230, 512), (340, 700), (512, 380), (684, 700), (794, 512), (914, 512)]
BEAD = (512, 380)
STROKE = 56
BEAD_R = 60
CORNER = 224            # iOS-like squircle radius on the 1024 master


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def svg_master(content_scale: float = 1.0, with_bg: bool = True) -> str:
    """The master SVG. content_scale shrinks the mark about the centre (used for the adaptive safe zone)."""
    t = f"translate(512 512) scale({content_scale}) translate(-512 -512)"
    pts = " ".join(f"{x},{y}" for x, y in W_PTS)
    bg = (
        f'<rect width="1024" height="1024" rx="{CORNER}" fill="{hex_of(BG)}"/>' if with_bg else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  {bg}
  <g transform="{t}">
    <polyline points="{pts}" fill="none" stroke="{hex_of(TEAL)}" stroke-opacity="0.22" stroke-width="{STROKE * 2}"
              stroke-linejoin="round" stroke-linecap="round"/>
    <polyline points="{pts}" fill="none" stroke="{hex_of(TEAL)}" stroke-width="{STROKE}"
              stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="{BEAD[0]}" cy="{BEAD[1]}" r="{BEAD_R + 22}" fill="{hex_of(AMBER)}" fill-opacity="0.25"/>
    <circle cx="{BEAD[0]}" cy="{BEAD[1]}" r="{BEAD_R}" fill="{hex_of(AMBER)}"/>
    <circle cx="{BEAD[0] - 20}" cy="{BEAD[1] - 20}" r="13" fill="{hex_of(WHITE)}" fill-opacity="0.85"/>
  </g>
</svg>
"""


def _thick_line(draw: ImageDraw.ImageDraw, a, b, width: int, fill) -> None:
    draw.line([a, b], fill=fill, width=width)
    r = width / 2
    for x, y in (a, b):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def render_png(size: int, with_bg: bool = True, content_scale: float = 1.0) -> Image.Image:
    """Supersampled raster of the master: 4x, then downscaled, for clean edges and a soft glow."""
    ss = 4
    s = size * ss
    k = s / 1024.0

    def pt(p):
        x = 512 + (p[0] - 512) * content_scale
        y = 512 + (p[1] - 512) * content_scale
        return (x * k, y * k)

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if with_bg:
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(CORNER * k), fill=BG + (255,))
        img = Image.alpha_composite(img, layer)

    pts = [pt(p) for p in W_PTS]
    w = int(STROKE * k * content_scale)

    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for a, b in zip(pts, pts[1:]):
        _thick_line(gd, a, b, w * 2, TEAL + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=int(24 * k * content_scale)))
    img = Image.alpha_composite(img, glow)

    line = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line)
    for a, b in zip(pts, pts[1:]):
        _thick_line(ld, a, b, w, TEAL + (255,))
    img = Image.alpha_composite(img, line)

    bx, by = pt(BEAD)
    br = BEAD_R * k * content_scale
    halo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([bx - br - 22 * k, by - br - 22 * k, bx + br + 22 * k, by + br + 22 * k],
                                 fill=AMBER + (70,))
    img = Image.alpha_composite(img, halo.filter(ImageFilter.GaussianBlur(radius=int(8 * k))))
    dot = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    dd.ellipse([bx - br, by - br, bx + br, by + br], fill=AMBER + (255,))
    gr = 13 * k * content_scale
    gx, gy = bx - 20 * k * content_scale, by - 20 * k * content_scale
    dd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=WHITE + (215,))
    img = Image.alpha_composite(img, dot)

    return img.resize((size, size), Image.LANCZOS)


def write_vector_foreground(path: pathlib.Path) -> None:
    """Adaptive foreground: the mark scaled into the 66% safe zone, on a transparent canvas, 108dp."""
    scale = 0.74
    bx, by = BEAD
    d = "M" + " L".join(f"{x},{y}" for x, y in W_PTS)
    path.write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by scripts/brand/make_icon.py. Edit the generator, not this file. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="1024"
    android:viewportHeight="1024">
    <group
        android:pivotX="512"
        android:pivotY="512"
        android:scaleX="{scale}"
        android:scaleY="{scale}">
        <path
            android:pathData="{d}"
            android:strokeColor="#{TEAL[0]:02X}{TEAL[1]:02X}{TEAL[2]:02X}"
            android:strokeAlpha="0.22"
            android:strokeWidth="{STROKE * 2}"
            android:strokeLineCap="round"
            android:strokeLineJoin="round" />
        <path
            android:pathData="{d}"
            android:strokeColor="#{TEAL[0]:02X}{TEAL[1]:02X}{TEAL[2]:02X}"
            android:strokeWidth="{STROKE}"
            android:strokeLineCap="round"
            android:strokeLineJoin="round" />
        <path
            android:fillColor="#{AMBER[0]:02X}{AMBER[1]:02X}{AMBER[2]:02X}"
            android:pathData="M{bx - BEAD_R},{by} a{BEAD_R},{BEAD_R} 0 1,0 {2 * BEAD_R},0 a{BEAD_R},{BEAD_R} 0 1,0 {-2 * BEAD_R},0" />
        <path
            android:fillColor="#FFFFFF"
            android:fillAlpha="0.85"
            android:pathData="M{bx - 20 - 13},{by - 20} a13,13 0 1,0 26,0 a13,13 0 1,0 -26,0" />
    </group>
</vector>
""", encoding="utf-8")


LEGACY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def main() -> None:
    brand = ROOT / "docs" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    (brand / "wagwatch-icon.svg").write_text(svg_master(), encoding="utf-8")
    render_png(512).save(brand / "wagwatch-icon-512.png")

    res = ROOT / "android" / "app" / "src" / "main" / "res"
    for density, px in LEGACY_SIZES.items():
        out = res / f"mipmap-{density}"
        out.mkdir(parents=True, exist_ok=True)
        render_png(px).save(out / "ic_launcher.png")
        render_png(px).save(out / "ic_launcher_round.png")

    (res / "drawable").mkdir(parents=True, exist_ok=True)
    write_vector_foreground(res / "drawable" / "ic_launcher_foreground.xml")

    (res / "values").mkdir(parents=True, exist_ok=True)
    (res / "values" / "ic_launcher_colors.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated by scripts/brand/make_icon.py -->\n'
        '<resources>\n'
        f'    <color name="ic_launcher_background">{hex_of(BG)}</color>\n'
        '</resources>\n',
        encoding="utf-8",
    )

    anydpi = res / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    entry = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated by scripts/brand/make_icon.py -->\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background"/>\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground"/>\n'
        '</adaptive-icon>\n'
    )
    (anydpi / "ic_launcher.xml").write_text(entry, encoding="utf-8")
    (anydpi / "ic_launcher_round.xml").write_text(entry, encoding="utf-8")

    web = ROOT / "frontend" / "app"
    (web / "icon.svg").write_text(svg_master(), encoding="utf-8")
    render_png(256).save(
        web / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("icon written")


if __name__ == "__main__":
    main()
