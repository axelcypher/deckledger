"""Renders a trade/sale sheet as an image: a grid of card images on a playmat-style background.

Kept free of Flask and the database so it can be tested and tuned on its own. app.py resolves
the card images and hands them in; everything here is layout and drawing with Pillow.

The backgrounds are drawn, not photographed: an own playmat look (cloth texture, stitched edge,
inner frame, element-coloured glow) in the colours of VCard's seven elements. No artwork or logo
of any publisher is used.
"""

from __future__ import annotations

import colorsys
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# Grid shapes by how many cards they hold, landscape like a playmat. A sheet takes the smallest
# shape that fits; beyond the largest one the cards continue on further pages.
LAYOUTS = [(1, 1), (2, 1), (3, 1), (2, 2), (3, 2), (4, 2), (5, 2), (4, 3), (5, 3), (6, 3), (6, 4), (7, 4), (8, 4), (8, 5)]
MAX_COLUMNS, MAX_ROWS = 10, 6

CARD_WIDTH = 300          # px at scale 1
CARD_RATIO = 7 / 5        # height / width of a trading card
SUPERSAMPLE = 3           # masks are drawn larger and scaled down for smooth edges

# name -> (label, top colour, bottom colour, accent, glow)
BACKGROUNDS = {
    "midnight": ("Mitternacht", "#151a24", "#0a0d13", "#c9a85a", "#3a4a66"),
    "fire": ("Feuer", "#3a1410", "#140606", "#f0a04a", "#c2371f"),
    "water": ("Wasser", "#0f2a44", "#06111f", "#8fd0f5", "#1f6fb5"),
    "grass": ("Pflanze", "#12301f", "#06140c", "#b9dd8a", "#2f8f52"),
    "electric": ("Elektro", "#33290b", "#131004", "#f6dd62", "#c79a12"),
    "platinum": ("Platin", "#2a2e35", "#0f1114", "#e6ebf2", "#7b8796"),
    "divine": ("Divine", "#3a3320", "#14110a", "#f7ecc4", "#c9ad5c"),
    "chaos": ("Chaos", "#261236", "#0b0612", "#d7a6f5", "#7a34b8"),
}
DEFAULT_BACKGROUND = "midnight"


def grid_for(count: int) -> tuple[int, int]:
    """Smallest layout that holds `count` cards (the largest one if none does)."""
    for columns, rows in LAYOUTS:
        if columns * rows >= count:
            return columns, rows
    return LAYOUTS[-1]


def parse_layout(layout: str | None) -> tuple[int, int] | None:
    """'5x3' -> (5, 3); 'auto' or anything unusable -> None."""
    try:
        columns, rows = (int(part) for part in str(layout or "").lower().split("x"))
    except ValueError:
        return None
    if 1 <= columns <= MAX_COLUMNS and 1 <= rows <= MAX_ROWS:
        return columns, rows
    return None


def paginate(count: int, layout: str | None = "auto") -> list[tuple[int, int, int]]:
    """Splits `count` cards into pages: [(columns, rows, cards_on_page), ...].

    With a fixed layout every page has that shape. With "auto" full pages use the largest
    layout and the last page the smallest one that fits what is left.
    """
    if count <= 0:
        return []
    fixed = parse_layout(layout)
    capacity = fixed[0] * fixed[1] if fixed else LAYOUTS[-1][0] * LAYOUTS[-1][1]
    pages = []
    remaining = count
    while remaining > 0:
        on_page = min(remaining, capacity)
        pages.append((*(fixed or grid_for(on_page)), on_page))
        remaining -= on_page
    return pages


FONT_PATH = Path(__file__).with_name("static") / "fonts" / "Manrope.ttf"


@lru_cache(maxsize=64)
def font(size: float, weight: int = 700) -> ImageFont.ImageFont:
    """Manrope, the typeface of the app itself (bundled, OFL). Pillow's built-in face has no
    "×", "–" or "€", which is exactly what quantities and price labels need."""
    size = max(8, round(size))
    try:
        face = ImageFont.truetype(str(FONT_PATH), size)
        face.set_variation_by_axes([weight])
        return face
    except OSError:
        return ImageFont.load_default(size=size)


def rounded_mask(size: tuple[int, int], radius: float) -> Image.Image:
    big = Image.new("L", (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius * SUPERSAMPLE, fill=255)
    return big.resize(size, Image.LANCZOS)


def vertical_gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    ramp = Image.linear_gradient("L").resize(size)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), ramp)


def mat_background(size: tuple[int, int], name: str) -> Image.Image:
    """A neoprene playmat seen from above: cloth grain, a glow in the element's colour, a faint
    diamond weave, and the stitched edge with an inner frame line."""
    _, top, bottom, accent, glow = BACKGROUNDS.get(name, BACKGROUNDS[DEFAULT_BACKGROUND])
    width, height = size
    unit = min(width, height)
    mat = vertical_gradient(size, top, bottom)

    # Soft light from the centre, in the element colour.
    light = Image.radial_gradient("L").resize((int(width * 1.5), int(height * 1.7)))
    light = ImageChops.invert(light).crop((int(width * .25), int(height * .35), int(width * .25) + width, int(height * .35) + height))
    mat = Image.composite(Image.new("RGB", size, glow), mat, light.point(lambda value: int(value * .42)))

    # Diamond weave, barely visible -- breaks up the flat gradient like printed cloth does.
    weave = Image.new("L", size, 0)
    draw = ImageDraw.Draw(weave)
    step = max(18, unit // 22)
    for offset in range(-height, width + height, step):
        draw.line((offset, 0, offset + height, height), fill=26, width=1)
        draw.line((offset, height, offset + height, 0), fill=26, width=1)
    mat = Image.composite(Image.new("RGB", size, accent), mat, weave)

    # Cloth grain.
    grain = Image.effect_noise(size, 22).point(lambda value: int(abs(value - 128) * .5))
    mat = Image.composite(Image.new("RGB", size, "#ffffff"), mat, grain.point(lambda value: min(value, 22)))

    # Darker towards the edge, then the mat's own rim: stitching and a thin inner frame.
    vignette = Image.radial_gradient("L").resize((int(width * 1.25), int(height * 1.25)))
    vignette = vignette.crop((int(width * .125), int(height * .125), int(width * .125) + width, int(height * .125) + height))
    mat = Image.composite(Image.new("RGB", size, "#000000"), mat, vignette.point(lambda value: int(max(0, value - 90) * .9)))

    overlay = Image.new("RGBA", (width * 2, height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    edge, frame, corner = unit * .028 * 2, unit * .05 * 2, unit * .045 * 2
    draw.rounded_rectangle((edge, edge, width * 2 - edge, height * 2 - edge), corner, outline=accent + "70", width=max(2, round(unit * .004)))
    dash, gap = unit * .022 * 2, unit * .014 * 2
    stitch_inset = edge * .55
    for x in frange(corner, width * 2 - corner, dash + gap):
        draw.line((x, stitch_inset, min(x + dash, width * 2 - corner), stitch_inset), fill=accent + "c0", width=max(2, round(unit * .0035)))
        draw.line((x, height * 2 - stitch_inset, min(x + dash, width * 2 - corner), height * 2 - stitch_inset), fill=accent + "c0", width=max(2, round(unit * .0035)))
    for y in frange(corner, height * 2 - corner, dash + gap):
        draw.line((stitch_inset, y, stitch_inset, min(y + dash, height * 2 - corner)), fill=accent + "c0", width=max(2, round(unit * .0035)))
        draw.line((width * 2 - stitch_inset, y, width * 2 - stitch_inset, min(y + dash, height * 2 - corner)), fill=accent + "c0", width=max(2, round(unit * .0035)))
    draw.rounded_rectangle((frame, frame, width * 2 - frame, height * 2 - frame), corner * .6, outline=accent + "38", width=max(1, round(unit * .002)))
    overlay = overlay.resize(size, Image.LANCZOS)
    mat = mat.convert("RGBA")
    mat.alpha_composite(overlay)
    return mat


def frange(start: float, stop: float, step: float):
    value = start
    while value < stop:
        yield value
        value += step


def fit_text(draw: ImageDraw.ImageDraw, text: str, size: float, max_width: float) -> tuple[str, ImageFont.ImageFont]:
    """Shrinks, then shortens, a line until it fits."""
    current = size
    while current > size * .6:
        face = font(current)
        if draw.textlength(text, font=face) <= max_width:
            return text, face
        current -= 2
    face = font(current)
    while len(text) > 4 and draw.textlength(text + "…", font=face) > max_width:
        text = text[:-1]
    return text.rstrip() + "…", face


# ---- Holo cards --------------------------------------------------------------------------------
# A scan of a holo card looks like its regular print, and on a sale sheet that difference is the
# price. Holo cards therefore get a rainbow sheen over the artwork and an iridescent frame.

PREMIUM_NAME = re.compile(r"manga|enchanted|verzaubert|iconic|epic|signature|signed")
PREMIUM_CODE = re.compile(r"(?:^|[\s_-])(our|osr|sec|sp|ur|sy)(?:$|[\s_-])")
FOIL_NAME = re.compile(r"foil|silver|satin|holo|rainbow|etched|textured|gold|parallel|alternate|alt art")


def is_holo(finish: str | None, variant_code: str | None = "", rarity: str | None = "", game_id: str | None = "", is_parallel=0) -> bool:
    """Whether a variant is a foil print of any kind. Same rules as finishPresentation() in
    static/js/finish.js, which decides where the app itself shows a foil effect."""
    finish = str(finish or "").strip()
    descriptor = f"{finish} {variant_code or ''} {rarity or ''}".lower()
    if PREMIUM_NAME.search(descriptor) or PREMIUM_CODE.search(descriptor) or FOIL_NAME.search(descriptor):
        return True
    if game_id == "hololive" and finish.lower() in ("s", "sr"):
        return True
    return bool(int(is_parallel or 0))


@lru_cache(maxsize=32)
def rainbow(size: tuple[int, int], cycles: float = 1.5, saturation: float = .6, angle: float = 32.0) -> Image.Image:
    """Diagonal spectrum, the way light breaks on foil."""
    width, height = size
    span = int((width ** 2 + height ** 2) ** .5) + 2
    ramp = Image.linear_gradient("L").resize((span, span)).rotate(angle, resample=Image.BICUBIC)
    left, top = (span - width) // 2, (span - height) // 2
    ramp = ramp.crop((left, top, left + width, top + height))
    tables = [[], [], []]
    for step in range(256):
        for table, value in zip(tables, colorsys.hsv_to_rgb((step / 255 * cycles + .5) % 1, saturation, 1)):
            table.append(round(value * 255))
    return Image.merge("RGB", [ramp.point(table) for table in tables])


@lru_cache(maxsize=8)
def glare(size: tuple[int, int]) -> Image.Image:
    """Two soft bands of light running across the card."""
    width, height = size
    bands = Image.new("L", size, 0)
    draw = ImageDraw.Draw(bands)
    for start, thickness, strength in ((.18, .13, 60), (.58, .06, 42)):
        x = width * (start + .5)
        draw.polygon([(x, 0), (x + width * thickness, 0), (x + width * thickness - height * .62, height), (x - height * .62, height)], fill=strength)
    return bands.filter(ImageFilter.GaussianBlur(width * .035))


def holo_sheen(image: Image.Image) -> Image.Image:
    """Lays the spectrum and the glare over a card. Near-black areas (borders, text boxes) stay
    as they are, like on the real card, where the foil only shows through the printed colours."""
    size = image.size
    colour = image.convert("RGB")
    lit = ImageChops.screen(colour, rainbow(size, saturation=.9).point(lambda value: int(value * .3)))
    lit = ImageChops.screen(lit, Image.merge("RGB", [glare(size)] * 3))
    printed = colour.convert("L").filter(ImageFilter.GaussianBlur(max(1, size[0] // 90)))
    printed = printed.point(lambda value: 0 if value <= 18 else (255 if value >= 70 else int((value - 18) * 255 / 52)))
    result = Image.composite(lit, colour, printed).convert("RGBA")
    result.putalpha(image.getchannel("A"))
    return result


@lru_cache(maxsize=8)
def holo_frame(size: tuple[int, int]) -> tuple[Image.Image, int]:
    """An iridescent rim around a card of `size`, with a faint glow so it also stands out on the
    light mats. Returns the image and how far it reaches beyond the card on every side."""
    width, height = size
    border = max(3, round(width * .022))
    pad = border * 3
    outer = (width + pad * 2, height + pad * 2)
    ring = Image.new("L", outer, 0)
    ring.paste(rounded_mask((width + border * 2, height + border * 2), width * .045 + border), (pad - border, pad - border))
    ring.paste(0, (pad, pad), rounded_mask(size, width * .045))
    colours = rainbow(outer, cycles=2.2, saturation=.78)
    frame = Image.new("RGBA", outer, (0, 0, 0, 0))
    glow = ring.filter(ImageFilter.GaussianBlur(border * .9)).point(lambda value: int(value * .7))
    frame.paste(colours, (0, 0), glow)
    frame.paste(ImageChops.screen(colours, Image.new("RGB", outer, "#3c3c3c")), (0, 0), ring)
    return frame, pad


# Print files: a 63 x 88 mm card with 3 mm bleed on every side. Publishers that put those files
# online show more border than the cut card has. app.py trims them once, for every view.
PRINT_BLEED = (3 / 69, 3 / 94)
CUT_CARD_RATIO = 63 / 88


def trim_bleed(image: Image.Image, bleed: tuple[float, float] | None) -> Image.Image:
    """Cuts the bleed (fractions of width and height per side) off an image -- but only off one
    that is wider in proportion than a cut card, so an already trimmed image is never cut twice."""
    if not bleed or image.width / image.height < CUT_CARD_RATIO + .008:
        return image
    left, top = round(image.width * bleed[0]), round(image.height * bleed[1])
    return image.crop((left, top, image.width - left, image.height - top))


def card_tile(card: dict, size: tuple[int, int]) -> Image.Image:
    """The card's own image, or a labelled stand-in when no image is available."""
    width, height = size
    radius = width * .045
    path = card.get("image_path")
    if path and Path(path).is_file():
        try:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            image = image.resize(size, Image.LANCZOS)
            # Scans come with square or with already-rounded, transparent corners; clipping to
            # the same rounded shape makes both look alike.
            image.putalpha(ImageChops.multiply(image.getchannel("A"), rounded_mask(size, radius)))
            return holo_sheen(image) if card.get("holo") else image
        except OSError:
            pass
    tile = Image.new("RGBA", size, "#1b2230")
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((width * .05, height * .04, width * .95, height * .96), radius, outline="#5b6880", width=max(1, width // 120))
    name, face = fit_text(draw, str(card.get("name") or "?"), width * .11, width * .8)
    draw.text((width / 2, height * .45), name, font=face, fill="#e8edf5", anchor="mm")
    meta, face = fit_text(draw, " · ".join(str(part) for part in (card.get("set_code"), card.get("number")) if part), width * .08, width * .8)
    draw.text((width / 2, height * .56), meta, font=face, fill="#9aa7bb", anchor="mm")
    tile.putalpha(rounded_mask(size, radius))
    return tile


def render_page(cards: list[dict], columns: int, rows: int, *, background: str = DEFAULT_BACKGROUND, kind: str = "WTS",
                title: str = "", subtitle: str = "", page: tuple[int, int] = (1, 1), scale: float = 1.0) -> Image.Image:
    """One page of a sheet. `cards` are dicts with image_path, name, set_code, number, quantity,
    label and holo -- already sorted and cut to this page."""
    card_w = max(60, round(CARD_WIDTH * scale))
    card_h = round(card_w * CARD_RATIO)
    gap = round(card_w * .09)
    margin = round(card_w * .34)
    labelled = any(card.get("label") for card in cards)
    row_h = card_h + (round(card_w * .2) if labelled else 0)
    header = round(card_w * .44)
    # A lone card or a single column would give a canvas too narrow for the heading.
    width = max(margin * 2 + columns * card_w + (columns - 1) * gap, round(card_w * 3.2))
    height = margin + header + rows * row_h + (rows - 1) * gap + margin
    canvas = mat_background((width, height), background)
    draw = ImageDraw.Draw(canvas)
    accent = BACKGROUNDS.get(background, BACKGROUNDS[DEFAULT_BACKGROUND])[3]

    # Heading: the kind as a badge, the sheet's name, and a note (user name, date) on the right.
    badge_font = font(card_w * .13)
    badge_text = kind.upper()
    badge_w = draw.textlength(badge_text, font=badge_font) + card_w * .16
    badge_box = (margin, margin * .78, margin + badge_w, margin * .78 + card_w * .24)
    draw.rounded_rectangle(badge_box, card_w * .05, fill=accent)
    draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2), badge_text, font=badge_font, fill="#101216", anchor="mm")
    page_note = f"{page[0]}/{page[1]}" if page[1] > 1 else ""
    right_text = " · ".join(part for part in (subtitle.strip(), page_note) if part)
    right_width = 0
    if right_text:
        right_text, right_font = fit_text(draw, right_text, card_w * .1, width * .4)
        right_width = draw.textlength(right_text, font=right_font)
        draw.text((width - margin, (badge_box[1] + badge_box[3]) / 2), right_text, font=right_font, fill=accent, anchor="rm")
    if title.strip():
        available = width - margin - badge_box[2] - card_w * .12 - right_width - card_w * .15
        text, title_font = fit_text(draw, title.strip(), card_w * .16, max(card_w, available))
        draw.text((badge_box[2] + card_w * .12, (badge_box[1] + badge_box[3]) / 2), text, font=title_font, fill="#f4f6fa", anchor="lm")

    # One soft shadow, reused under every card.
    blur = max(2, card_w // 30)
    shadow = Image.new("RGBA", (card_w + blur * 6, card_h + blur * 6), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((blur * 3, blur * 3, blur * 3 + card_w, blur * 3 + card_h), card_w * .045, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))

    grid_w = columns * card_w + (columns - 1) * gap
    top = margin + header
    for index, card in enumerate(cards[: columns * rows]):
        row, column = divmod(index, columns)
        in_row = min(columns, len(cards) - row * columns)
        # A last row that is not full is centred instead of hanging on the left.
        row_w = in_row * card_w + (in_row - 1) * gap
        x = (width - grid_w) // 2 + (grid_w - row_w) // 2 + column * (card_w + gap)
        y = top + row * (row_h + gap)
        canvas.alpha_composite(shadow, (x - blur * 3, y - blur * 3 + blur))
        canvas.alpha_composite(card_tile(card, (card_w, card_h)), (x, y))
        if card.get("holo"):
            frame, reach = holo_frame((card_w, card_h))
            canvas.alpha_composite(frame, (x - reach, y - reach))
        quantity = int(card.get("quantity") or 1)
        if quantity > 1:
            text = f"×{quantity}"
            face = font(card_w * .12)
            radius = max(draw.textlength(text, font=face) / 2 + card_w * .05, card_w * .11)
            cx, cy = x + card_w - radius * .7, y + radius * .7
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill="#101216", outline=accent, width=max(2, card_w // 90))
            draw.text((cx, cy), text, font=face, fill="#ffffff", anchor="mm")
        if card.get("label"):
            text, face = fit_text(draw, str(card["label"]), card_w * .1, card_w * .92)
            text_w = draw.textlength(text, font=face)
            cy = y + card_h + card_w * .11
            draw.rounded_rectangle((x + card_w / 2 - text_w / 2 - card_w * .05, cy - card_w * .075, x + card_w / 2 + text_w / 2 + card_w * .05, cy + card_w * .075), card_w * .04, fill=(16, 18, 22, 215))
            draw.text((x + card_w / 2, cy), text, font=face, fill=accent, anchor="mm")
    return canvas


def swatch(name: str, size: tuple[int, int] = (240, 150)) -> Image.Image:
    """Small preview of a background for the picker."""
    return mat_background(size, name).convert("RGB")


def page_slices(cards: list[dict], layout: str | None = "auto") -> list[tuple[int, int, list[dict]]]:
    pages, start = [], 0
    for columns, rows, on_page in paginate(len(cards), layout):
        pages.append((columns, rows, cards[start:start + on_page]))
        start += on_page
    return pages


def scale_for(columns: int, max_width: int = 3600) -> float:
    """Keeps the widest layouts within a size image hosts accept without shrinking small sheets."""
    full = columns * CARD_WIDTH * 1.09 + CARD_WIDTH * .68
    return min(1.0, max_width / full)
