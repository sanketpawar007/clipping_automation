import sys
from PIL import Image, ImageDraw, ImageFont
OUT = sys.argv[1]
BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
PINK, PURPLE, CYAN, WHITE, NIGHT = (255, 64, 160), (76, 29, 149), (34, 211, 238), (255, 255, 255), (15, 10, 40)

def vgrad(w, h, stops):
    img = Image.new("RGB", (w, h)); d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1); n = len(stops) - 1; i = min(int(t * n), n - 1); f = t * n - i
        c = tuple(int(stops[i][k] + (stops[i+1][k] - stops[i][k]) * f) for k in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img

def centered(d, text, font, cx, cy, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text((cx - (r - l) / 2 - l, cy - (b - t) / 2 - t), text, font=font, fill=fill)

def sun(d, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 196, 70))
    for i, y in enumerate(range(int(cy + r * 0.15), int(cy + r), max(6, r // 9))):  # retro stripes
        d.rectangle([cx - r, y, cx + r, y + 4 + i * 2], fill=None, outline=None)

S = 1000
img = vgrad(S, S, [NIGHT, PURPLE, PINK]); d = ImageDraw.Draw(img)
d.ellipse([S/2 - 300, S/2 - 300, S/2 + 300, S/2 + 300], fill=(255, 140, 90))
for i, y in enumerate(range(560, 800, 34)):   # retro sun stripes
    d.rectangle([S/2 - 300, y, S/2 + 300, y + 6 + i * 3], fill=PINK)
d.ellipse([S/2 - 400, S/2 - 400, S/2 + 400, S/2 + 400], outline=CYAN, width=26)
centered(d, "VV", ImageFont.truetype(BLACK, 330), S/2 + 6, S/2 - 4, NIGHT)   # shadow
centered(d, "VV", ImageFont.truetype(BLACK, 330), S/2, S/2 - 10, WHITE)
img.save(f"{OUT}/vicevision_profile.png")

W, H = 2560, 1440
b = vgrad(W, H, [NIGHT, PURPLE, PINK]); d = ImageDraw.Draw(b)
d.ellipse([W/2 - 380, H/2 - 330, W/2 + 380, H/2 + 430], fill=(255, 140, 90))
for i, y in enumerate(range(int(H/2 + 60), int(H/2 + 430), 40)):
    d.rectangle([W/2 - 380, y, W/2 + 380, y + 6 + i * 3], fill=PINK)
centered(d, "VICE VISION", ImageFont.truetype(BLACK, 150), W/2 + 6, H/2 - 54, NIGHT)
centered(d, "VICE VISION", ImageFont.truetype(BLACK, 150), W/2, H/2 - 60, WHITE)
d.rounded_rectangle([W/2 - 600, H/2 + 38, W/2 + 600, H/2 + 114], 36, fill=NIGHT)
centered(d, "GTA 6 moments, edits & stream clips  |  daily", ImageFont.truetype(BOLD, 52), W/2, H/2 + 75, CYAN)
b.save(f"{OUT}/vicevision_banner.png")
print("ok")
