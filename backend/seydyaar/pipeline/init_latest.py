"""Initialize docs/latest with safe placeholder files.

This is NOT a demo generator.
It only ensures the PWA has an empty-but-valid `meta_index.json` and a placeholder preview image,
so the front-end never crashes when no runs exist yet.
"""

from __future__ import annotations

import json
from pathlib import Path


def init_latest(out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # meta_index.json (empty)
    idx_path = out / "meta_index.json"
    if not idx_path.exists():
        idx_path.write_text(json.dumps({"latest_run_id": None, "runs": []}, indent=2), encoding="utf-8")

    # placeholder preview.png
    prev = out / "preview.png"
    if not prev.exists():
        try:
            from PIL import Image, ImageDraw, ImageFont

            w, h = 900, 520
            img = Image.new("RGB", (w, h), (3, 36, 59))
            d = ImageDraw.Draw(img)
            for y in range(h):
                t = y / (h - 1)
                r = int(3 + 2 * t)
                g = int(22 + 30 * t)
                b = int(36 + 80 * t)
                d.line([(0, y), (w, y)], fill=(r, g, b))

            # glow
            for cx, cy, rad, alpha in [(180, 120, 180, 60), (720, 150, 220, 50), (450, 420, 260, 55)]:
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                od.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(69, 201, 255, alpha))
                img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

            d = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 36)
                font2 = ImageFont.truetype("DejaVuSans.ttf", 18)
            except Exception:
                font = ImageFont.load_default()
                font2 = ImageFont.load_default()

            t1 = "No data yet"
            t2 = "Generate outputs into docs/latest to see maps, hotspots, and uncertainty." 
            bb = d.textbbox((0, 0), t1, font=font)
            d.text(((w - (bb[2] - bb[0])) // 2, h // 2 - 50), t1, font=font, fill=(232, 246, 255))
            bb2 = d.textbbox((0, 0), t2, font=font2)
            d.text(((w - (bb2[2] - bb2[0])) // 2, h // 2 + 10), t2, font=font2, fill=(168, 198, 214))

            img.save(prev, format="PNG", optimize=True)
        except Exception:
            # If pillow is missing, just skip.
            pass

    # README
    readme = out / "README.md"
    if not readme.exists():
        readme.write_text(
            "# docs/latest (generated outputs)\n\n"
            "This folder is runtime output for the Seyd‑Yaar pipeline.\n\n"
            "The repo keeps only small placeholders so the PWA doesn't crash when no runs exist.\n",
            encoding="utf-8",
        )

    return out
