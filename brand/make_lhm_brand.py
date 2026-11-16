import sys
from PIL import Image, ImageDraw, ImageFont
OUT = sys.argv[1]
BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
MAROON, NAVY = (122, 18, 32), (15, 23, 42)
CREAM, GOLD = (250, 243, 224), (234, 179, 8)

def gradient(w, h, c1, c2):
    img = Image.new("RGB", (w, h)); px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img

def centered(d, text, font, cx, cy, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text((cx - (r - l) / 2 - l, cy - (b - t) / 2 - t), text, font=font, fill=fill)

# profile: works when cropped to a circle (keep content inside ~80% diameter)
S = 1000
img = gradient(S, S, MAROON, NAVY); d = ImageDraw.Draw(img)
r = int(S * 0.40); w = int(S * 0.03)
d.ellipse([S/2 - r, S/2 - r, S/2 + r, S/2 + r], outline=GOLD, width=w)
centered(d, "LHM", ImageFont.truetype(BLACK, 250), S/2, S/2 - 60, CREAM)
d.rounded_rectangle([S/2 - 190, S/2 + 85, S/2 + 190, S/2 + 97], 6, fill=GOLD)
centered(d, "TRUTH BOMBS", ImageFont.truetype(BLACK, 78), S/2, S/2 + 160, GOLD)
img.save(f"{OUT}/lhmtruthbombs_profile.png")

# banner 2560x1440, text inside central 1546x423 safe area
W, H = 2560, 1440
b = gradient(W // 4, H // 4, MAROON, NAVY).resize((W, H), Image.LANCZOS); d = ImageDraw.Draw(b)
centered(d, "LHM TRUTH BOMBS", ImageFont.truetype(BLACK, 122), W/2, H/2 - 60, CREAM)
centered(d, "Dating, marriage, faith & hard truths  |  daily clips", ImageFont.truetype(BOLD, 48), W/2, H/2 + 70, GOLD)
d.rounded_rectangle([W/2 - 150, H/2 + 125, W/2 + 150, H/2 + 135], 5, fill=GOLD)
b.save(f"{OUT}/lhmtruthbombs_banner.png")
print("ok")
