"""Makes assets/lhm_watermark.png: a play-button badge plus @lethimmansplain on a clear background.
Run: uv run python brand/make_lhm_watermark.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "lhm_watermark.png"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SCALE = 3                       # draw large, the renderer scales it down
H = 64 * SCALE

font = ImageFont.truetype(FONT, 40 * SCALE)
text = "@lethimmansplain"
text_w = int(font.getlength(text))
badge_w, badge_h = 84 * SCALE, 58 * SCALE
gap, pad = 14 * SCALE, 6 * SCALE
img = Image.new("RGBA", (pad * 2 + badge_w + gap + text_w, H + pad * 2), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

by = pad + (H - badge_h) // 2
d.rounded_rectangle([pad, by, pad + badge_w, by + badge_h], radius=16 * SCALE, fill=(255, 0, 0, 255))
cx, cy, t = pad + badge_w // 2, by + badge_h // 2, 13 * SCALE
d.polygon([(cx - t * 0.8, cy - t), (cx - t * 0.8, cy + t), (cx + t * 1.1, cy)], fill=(255, 255, 255, 255))

tx = pad + badge_w + gap
box = d.textbbox((0, 0), text, font=font)
ty = pad + (H - (box[3] - box[1])) // 2 - box[1]
d.text((tx, ty), text, font=font, fill=(255, 255, 255, 255),
       stroke_width=2 * SCALE, stroke_fill=(0, 0, 0, 200))

OUT.parent.mkdir(exist_ok=True)
img.save(OUT)
print(OUT, img.size)
