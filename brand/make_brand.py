import math, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
OUT = sys.argv[1]
BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

def gradient(w, h, c1, c2):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img

def centered(draw, text, font, cx, cy, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (r - l) / 2 - l, cy - (b - t) / 2 - t), text, font=font, fill=fill)

def ring_arrow(draw, cx, cy, rad, width, color, gap_deg=50):
    start, end = -90 + gap_deg / 2, 270 - gap_deg / 2
    draw.arc([cx - rad, cy - rad, cx + rad, cy + rad], start, end, fill=color, width=width)
    # arrowhead at the end of the arc, pointing clockwise
    a = math.radians(end)
    ex, ey = cx + rad * math.cos(a), cy + rad * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)  # tangent (clockwise)
    nx, ny = math.cos(a), math.sin(a)
    s = width * 1.6
    pts = [(ex + tx * s * 1.3, ey + ty * s * 1.3), (ex + nx * s, ey + ny * s), (ex - nx * s, ey - ny * s)]
    draw.polygon(pts, fill=color)

def logo(name, c1, c2, ring, letters, letter_color, size=1000):
    img = gradient(size, size, c1, c2)
    d = ImageDraw.Draw(img)
    cx = cy = size // 2
    ring_arrow(d, cx, cy, int(size * 0.36), int(size * 0.045), ring)
    font = ImageFont.truetype(BLACK, int(size * 0.30))
    centered(d, letters, font, cx, cy + size * 0.01, letter_color)
    img.save(f"{OUT}/{name}_profile.png")

def banner(name, c1, c2, accent, title, tagline, w=2560, h=1440):
    img = gradient(w // 4, h // 4, c1, c2).resize((w, h), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    # safe area for all devices is the central 1546x423
    centered(d, title, ImageFont.truetype(BLACK, 170), w / 2, h / 2 - 70, (255, 255, 255))
    centered(d, tagline, ImageFont.truetype(BOLD, 64), w / 2, h / 2 + 95, accent)
    d.rounded_rectangle([w / 2 - 150, h / 2 + 160, w / 2 + 150, h / 2 + 170], 5, fill=accent)
    img.save(f"{OUT}/{name}_banner.png")

logo("hypereplay", (109, 40, 217), (244, 63, 94), (255, 255, 255), "HR", (255, 255, 255))
banner("hypereplay", (46, 16, 101), (190, 18, 60), (253, 224, 71), "HYPE REPLAY", "Gaming & streamer moments, daily")
logo("moneycuts", (2, 20, 14), (6, 78, 59), (34, 197, 94), "MC", (255, 255, 255))
banner("moneycuts", (2, 20, 14), (6, 95, 70), (34, 197, 94), "MONEY CUTS", "Business, money & podcast clips, daily")
print("ok")
