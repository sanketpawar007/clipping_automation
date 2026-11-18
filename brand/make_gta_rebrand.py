"""Rebrand Hype Replay and Money Cuts (-> Six Decoded) as GTA VI pages.

Each page gets its own colour so none looks like Vice Vision (pink/purple sunset):
  Hype Replay  -> red + black   (stream chaos / funny moments)
  Six Decoded  -> teal + navy   (news / secrets / hidden details)
Artwork: Rockstar's official GTA VI wallpapers (no logo), rockstargames.com/VI/media.
Run: uv run --with pillow python make_gta_rebrand.py   (from brand/)
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
FONT2 = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

PAGES = {
    "hypereplay": dict(
        art="rockstar_jason_lucia_robbery_square.jpg",
        crop=(470, 179, 1747, 1456),
        ring=(255, 45, 70), dark=(12, 6, 8),
        title="HYPE REPLAY",
        sub="GTA 6 stream chaos, fails & funniest moments  |  daily",
    ),
    "sixdecoded": dict(
        art="rockstar_leonida_keys_square.jpg",
        crop=(648, 432, 1998, 1782),
        ring=(0, 214, 200), dark=(6, 16, 34),
        title="SIX DECODED",
        sub="GTA 6 news, secrets & hidden details  |  daily",
    ),
}


def avatar(p, name):
    art = Image.open(p["art"]).convert("RGB").crop(p["crop"]).resize((800, 800), Image.LANCZOS)
    out = Image.new("RGB", (800, 800), p["dark"])
    inner = 700
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, inner, inner], fill=255)
    out.paste(art.resize((inner, inner), Image.LANCZOS), (50, 50), mask)
    d = ImageDraw.Draw(out)
    d.ellipse([28, 28, 772, 772], outline=p["ring"], width=22)
    out.save(f"{name}_profile_gta6.png")


def banner(p, name):
    W, H = 2560, 1440
    art = Image.open(p["art"]).convert("RGB")
    art = art.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W - H) // 2 + H))
    art = ImageEnhance.Brightness(art.filter(ImageFilter.GaussianBlur(6))).enhance(0.45)
    tint = Image.new("RGB", (W, H), p["dark"])
    img = Image.blend(art, tint, 0.35)
    d = ImageDraw.Draw(img)
    # mobile safe area is the central 1546x423
    f1 = ImageFont.truetype(FONT, 150)
    f2 = ImageFont.truetype(FONT2, 50)
    tw = d.textlength(p["title"], font=f1)
    d.text(((W - tw) / 2 + 6, H / 2 - 160 + 6), p["title"], font=f1, fill=(0, 0, 0))
    d.text(((W - tw) / 2, H / 2 - 160), p["title"], font=f1, fill=(255, 255, 255))
    sw = d.textlength(p["sub"], font=f2)
    d.rounded_rectangle([W / 2 - sw / 2 - 40, H / 2 + 38, W / 2 + sw / 2 + 40, H / 2 + 114], 36, fill=p["ring"])
    d.text(((W - sw) / 2, H / 2 + 48), p["sub"], font=f2, fill=p["dark"])
    d.rectangle([W / 2 - 300, H / 2 - 175, W / 2 + 300, H / 2 - 165], fill=p["ring"])
    img.save(f"{name}_banner_gta6.png", optimize=True)
    print(name, "title width", tw, "sub width", sw, "(safe width 1546)")


for name, p in PAGES.items():
    avatar(p, name)
    banner(p, name)
