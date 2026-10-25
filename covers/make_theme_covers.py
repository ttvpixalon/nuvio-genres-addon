#!/usr/bin/env python3
"""Theme covers in the Xperience 'kaptain' style (real film still, dark bottom gradient, coloured left edge, heavy
uppercase label bottom-left). Writes {key}.webp (tile), {key}.hero.webp (clean backdrop) and {key}.logo.png (title).
Usage: make_theme_covers.py BACKDROP_DIR OUT_DIR"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
FONT = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
W, H = 1920, 1080
LABEL_X, LABEL_BASELINE, CAP_H = 113, 882, 88          # measured on kaptain/genres.action.webp
LABEL_FILL = (205, 205, 205)

# key, label, film still (IMDb id -> metahub backdrop), accent colour, optional crop focus (0..1 across the width)
THEMES = [
    ("mindfuck", "Mindfuck", "tt1375666", (150, 85, 225), 0.5),           # Inception
    ("plot_twists", "Plot Twists", "tt0114814", (70, 180, 190), 0.5),     # The Usual Suspects
    ("heist", "Heist", "tt0113277", (225, 175, 60), 0.5),                 # Heat
    ("time_travel", "Time Travel", "tt0088763", (235, 125, 45), 0.55),    # Back to the Future
    ("superhero", "Superhero", "tt0468569", (217, 51, 50), 0.5),          # The Dark Knight
    ("zombies", "Zombies", "tt5700672", (95, 175, 70), 0.5),              # Train to Busan
    ("apocalypse", "Apocalypse", "tt1392190", (225, 140, 35), 0.5),       # Mad Max: Fury Road
    ("serial_killer", "Serial Killer", "tt0114369", (175, 35, 45), 0.5),  # Se7en
    ("whodunnit", "Whodunnit", "tt8946378", (205, 65, 125), 0.5),         # Knives Out
    ("time_loop", "Time Loop", "tt0107048", (65, 135, 225), 0.5),         # Groundhog Day
    ("modern_horror", "Modern Horror", "tt5052448", (205, 35, 65), 0.5),  # Get Out
    ("horror_classics", "Horror Classics", "tt0081505", (160, 40, 50), 0.5),  # The Shining
    ("outer_space", "Outer Space", "tt0816692", (75, 170, 230), 0.45),    # Interstellar
    ("true_crime", "True Crime", "tt5290382", (200, 200, 200), 0.5),      # Mindhunter
]


def cover(img: Image.Image, w: int, h: int, focus: float) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x = int((img.width - w) * focus)
    y = (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def vertical_gradient(w: int, h: int, start: float, strength: int) -> Image.Image:
    """Black overlay, transparent above `start` (fraction of height), ramping to `strength` alpha at the bottom."""
    g = Image.new("L", (w, h), 0)
    px = g.load()
    y0 = int(h * start)
    for y in range(y0, h):
        t = (y - y0) / max(1, h - y0)
        a = int(strength * (t ** 1.4))
        for x in range(w):
            px[x, y] = a
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    black.putalpha(g)
    return black


def left_accent(w: int, h: int, color: tuple[int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle((0, 0, 12, h), fill=color + (255,))                 # solid bar
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle((0, 0, 70, h), fill=color + (150,))
    layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
    return layer


def label_font(text: str) -> ImageFont.FreeTypeFont:
    size = 10
    while True:
        f = ImageFont.truetype(FONT, size)
        l, t, r, b = f.getbbox("H")
        if (b - t) >= CAP_H:
            return f
        size += 1


def draw_label(canvas: Image.Image, text: str) -> None:
    f = label_font(text)
    l, t, r, b = f.getbbox(text.upper())
    x = LABEL_X - l
    y = LABEL_BASELINE - f.getbbox("H")[3]
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 4, y + 6), text.upper(), font=f, fill=(0, 0, 0, 180))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    ImageDraw.Draw(canvas).text((x, y), text.upper(), font=f, fill=LABEL_FILL + (255,))


def logo(text: str) -> Image.Image:
    f = label_font(text)
    l, t, r, b = f.getbbox(text.upper())
    pad = 40
    img = Image.new("RGBA", (r - l + pad * 2, b - t + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((pad - l + 4, pad - t + 6), text.upper(), font=f, fill=(0, 0, 0, 170))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    ImageDraw.Draw(img).text((pad - l, pad - t), text.upper(), font=f, fill=(235, 235, 235, 255))
    return img


for key, label, imdb, color, focus in THEMES:
    src = Image.open(SRC / f"{imdb}.jpg").convert("RGB")
    base = cover(src, W, H, focus).convert("RGBA")
    base.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 45)))            # gentle overall dim
    hero = base.copy()
    hero.alpha_composite(vertical_gradient(W, H, 0.55, 150))
    hero.convert("RGB").save(OUT / f"{key}.hero.webp", "WEBP", quality=86)
    base.alpha_composite(vertical_gradient(W, H, 0.35, 240))                  # dark bed for the label
    base.alpha_composite(left_accent(W, H, color))
    draw_label(base, label)
    base.convert("RGB").save(OUT / f"{key}.webp", "WEBP", quality=88)
    logo(label).save(OUT / f"{key}.logo.png", "PNG")
    print("made", key)

# preview sheet
tiles = [Image.open(OUT / f"{k}.webp").resize((480, 270)) for k, *_ in THEMES]
sheet = Image.new("RGB", (480 * 4 + 30, 270 * 4 + 30), (0, 0, 0))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % 4) * 490, (i // 4) * 280))
sheet.save(OUT / "_preview.jpg", quality=85)
print("preview", OUT / "_preview.jpg")
