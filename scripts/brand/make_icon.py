#!/usr/bin/env python3
"""Generate the WagWatch app icon from one design: the cute dog paw.

Colours come from the dashboard theme (frontend/app/globals.css):
  Background: #0F1113
  Accent (paw mark): #3FB8C1 (Teal)

Writes:
  docs/brand/wagwatch-icon.svg                     master (1024 viewBox)
  docs/brand/wagwatch-icon-512.png                 store/preview size
  android/app/src/main/res/mipmap-*/ic_launcher*.png   legacy launcher (pre-API 26)
  android/app/src/main/res/drawable/ic_launcher_foreground.xml   adaptive foreground (vector)
  android/app/src/main/res/values/ic_launcher_colors.xml         adaptive background colour
  android/app/src/main/res/mipmap-anydpi-v26/ic_launcher*.xml    adaptive icon entries
  frontend/app/icon.svg                            Next.js app icon (served as the favicon)
  frontend/public/icon.svg                         Public asset copy
  frontend/app/favicon.ico                         multi-size fallback

Run from the repo root:  python3 scripts/brand/make_icon.py   (needs Pillow)
"""
from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[2]
BG = (15, 17, 19)        # #0F1113 --background of the dark theme
TEAL = (63, 184, 193)    # #3FB8C1 --accent (dark theme)
CORNER = 224            # iOS/Android squircle radius on the 1024 master

# Palm pad cubic bezier segments on 1024 grid
PALM_SEGS = [
    ((300, 620), (330, 510), (450, 480), (512, 490)),
    ((512, 490), (574, 480), (694, 510), (724, 620)),
    ((724, 620), (734, 690), (684, 755), (630, 755)),
    ((630, 755), (575, 755), (545, 715), (512, 715)),
    ((512, 715), (479, 715), (449, 755), (394, 755)),
    ((394, 755), (340, 755), (290, 690), (300, 620)),
]

PALM_PATH_D = (
    "M 300,620 "
    "C 330,510 450,480 512,490 "
    "C 574,480 694,510 724,620 "
    "C 734,690 684,755 630,755 "
    "C 575,755 545,715 512,715 "
    "C 479,715 449,755 394,755 "
    "C 340,755 290,690 300,620 Z"
)

# 4 Toe Beans: (cx, cy, rx, ry, rot_deg)
TOES = [
    (245, 410, 62, 95, -30),
    (410, 280, 70, 115, -10),
    (614, 280, 70, 115, 10),
    (779, 410, 62, 95, 30),
]


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def cubic_bezier(p0, p1, p2, p3, n=30):
    pts = []
    for i in range(n + 1):
        t = i / n
        omt = 1 - t
        x = omt**3 * p0[0] + 3 * omt**2 * t * p1[0] + 3 * omt * t**2 * p2[0] + t**3 * p3[0]
        y = omt**3 * p0[1] + 3 * omt**2 * t * p1[1] + 3 * omt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def svg_master(content_scale: float = 1.0, with_bg: bool = True) -> str:
    """The master SVG. content_scale shrinks the mark about the centre."""
    t = f"translate(512 512) scale({content_scale}) translate(-512 -512)"
    bg = (
        f'<rect width="1024" height="1024" rx="{CORNER}" fill="{hex_of(BG)}"/>' if with_bg else ""
    )
    toes_svg = "\n    ".join(
        f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({rot} {cx} {cy})" fill="{hex_of(TEAL)}"/>'
        for cx, cy, rx, ry, rot in TOES
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  {bg}
  <g transform="{t}">
    <!-- Palm Pad -->
    <path d="{PALM_PATH_D}" fill="{hex_of(TEAL)}"/>
    <!-- Toe Beans -->
    {toes_svg}
  </g>
</svg>
"""


def _draw_rotated_ellipse(target: Image.Image, cx: float, cy: float, rx: float, ry: float, angle_deg: float, fill) -> None:
    pad = int(max(rx, ry) * 1.5)
    temp = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(temp)
    d.ellipse([pad - rx, pad - ry, pad + rx, pad + ry], fill=fill)
    rotated = temp.rotate(-angle_deg, resample=Image.BICUBIC)
    target.alpha_composite(rotated, (int(cx - pad), int(cy - pad)))


def render_png(size: int, with_bg: bool = True, content_scale: float = 1.0) -> Image.Image:
    """Supersampled raster of the master: 4x, then downscaled, for crisp edges and soft glow."""
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

    # Scaled palm polygon points
    poly = []
    for p0, p1, p2, p3 in PALM_SEGS:
        poly.extend(cubic_bezier(pt(p0), pt(p1), pt(p2), pt(p3)))

    # Soft ambient glow
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(poly, fill=TEAL + (75,))
    for cx, cy, rx, ry, rot in TOES:
        scx, scy = pt((cx, cy))
        _draw_rotated_ellipse(glow, scx, scy, rx * k * content_scale, ry * k * content_scale, rot, TEAL + (75,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=int(28 * k * content_scale)))
    img = Image.alpha_composite(img, glow)

    # Crisp solid paw
    paw = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(paw).polygon(poly, fill=TEAL + (255,))
    for cx, cy, rx, ry, rot in TOES:
        scx, scy = pt((cx, cy))
        _draw_rotated_ellipse(paw, scx, scy, rx * k * content_scale, ry * k * content_scale, rot, TEAL + (255,))
    img = Image.alpha_composite(img, paw)

    return img.resize((size, size), Image.LANCZOS)


def write_vector_foreground(path: pathlib.Path) -> None:
    """Adaptive foreground: the mark scaled into the 66% safe zone, on a transparent canvas, 108dp."""
    scale = 0.70
    toes_xml = []
    for cx, cy, rx, ry, rot in TOES:
        d = f"M {cx - rx},{cy} a {rx},{ry} 0 1,0 {2 * rx},0 a {rx},{ry} 0 1,0 {-2 * rx},0"
        toes_xml.append(f"""        <group
            android:pivotX="{cx}"
            android:pivotY="{cy}"
            android:rotation="{rot}">
            <path
                android:fillColor="{hex_of(TEAL)}"
                android:pathData="{d}" />
        </group>""")
    toes_str = "\n".join(toes_xml)

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
        <!-- Palm Pad -->
        <path
            android:fillColor="{hex_of(TEAL)}"
            android:pathData="{PALM_PATH_D}" />
        <!-- Toe Beans -->
{toes_str}
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
    (ROOT / "frontend" / "public" / "icon.svg").write_text(svg_master(), encoding="utf-8")
    render_png(256).save(
        web / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("cute paw icon written successfully across Android and Web")


if __name__ == "__main__":
    main()

