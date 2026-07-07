"""Generates icon.ico matching the in-app tray icon style (teal mic bars on dark rounded square)."""
from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]

def render(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, size // 5)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(10, 10, 20, 255))

    bar_color = (3, 218, 198, 255)
    fracs = [0.30, 0.55, 0.85, 0.55, 0.30]
    n = len(fracs)
    bw = max(1, size // 11)
    gap = max(1, size // 16)
    total_w = n * bw + (n - 1) * gap
    sx = (size - total_w) // 2
    bar_r = max(1, bw // 3)
    for i, frac in enumerate(fracs):
        h = max(2, int(frac * size * 0.75))
        x0 = sx + i * (bw + gap)
        y0 = (size - h) // 2
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + h], radius=bar_r, fill=bar_color)
    return img

base = render(256)
base.save("assets/icon.ico", sizes=[(s, s) for s in SIZES])
print("wrote assets/icon.ico")
