"""Draw the JMangaShelf mark on a 64-unit grid and export its public assets."""
from pathlib import Path
from PIL import Image, ImageDraw

directory = Path(__file__).resolve().parents[1] / "static" / "icons"
directory.mkdir(exist_ok=True)
scale = 16
icon = Image.new("RGBA", (64 * scale, 64 * scale))
draw = ImageDraw.Draw(icon)


def rounded(box, radius, color):
    draw.rounded_rectangle(tuple(x * scale for x in box), radius * scale, fill=color)


def polygon(points, color):
    draw.polygon([(x * scale, y * scale) for x, y in points], fill=color)


# The right book rests against the upright spines. The shelf curls into a J.
rounded((1, 1, 63, 63), 14, "#222033")
rounded((12, 16, 19, 40), 2, "#ceb8ff")
rounded((23, 11, 31, 40), 2, "#aa87ff")
polygon([(34, 19), (42, 16), (50, 37), (42, 40)], "#8ce1cb")
rounded((10, 44, 54, 50), 3, "#ebe4ff")
rounded((47, 39, 54, 50), 3, "#ebe4ff")
for size in (32, 180, 192, 512):
    icon.resize((size, size), Image.Resampling.LANCZOS).save(directory / f"marca-{size}.png")
icon.save(directory / "JMangaShelf.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
