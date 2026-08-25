"""Renders the branded photo cards a listing carousel is made of.

Until now a listing post was the scraped photos, unaltered: no price, no place,
no brand. That is the one format on this account that already earns, and it was
the only one leaving the two facts that sell an akiya — what it costs and where
it is — in a caption Instagram truncates at ~125 characters and most people
never expand. A photo saved out of the carousel said nothing about us at all.

So the photos are drawn onto cards first, in the same dark/brass language as the
text cards in cards.py and the reels: the price at the size it deserves, the
place under it, and a slim price + brand line on the later slides so any single
image that gets reshared still carries both.

LAYOUT. Cards are 1080x1350 (4:5), and listing photos are overwhelmingly
landscape — 600x450 is what homes.jp serves. Cover-cropping one of those to a
full 4:5 would throw away a third of the frame, so each slide asks for a band of
a certain depth instead: the hero for 62% of the card, leaving a panel deep
enough for the price, the place and the size; the later slides for the whole
thing, and they get as much of it as the photo can afford. A big portrait photo
does fill the card, and then the type moves onto a gradient over the bottom of
it. One code path; the photo decides which case it is.

PIL for the reason cards.py gives: a still costs a few megabytes where the reel
builder has already been OOM-killed on this VPS.
"""

import logging
import os
import re
import time

from PIL import Image, ImageChops, ImageDraw

from social.content.cards import (
    ACCENT,
    BG,
    BRAND,
    CARD_HEIGHT,
    CARD_WIDTH,
    FG,
    MUTED,
    _block_height,
    _bold,
    _draw_lines,
    _fit,
    _font,
    _light,
    _wrap,
)

logger = logging.getLogger(__name__)

MARGIN = 72

# Listing photos are small: homes.jp serves them through a proxy that caps at
# 600x600, so filling a 1080-wide card means ~1.8x. That is not a choice we can
# avoid — Instagram upscales a 600px image into the same slot itself, and doing
# it here with Lanczos is the better of the two. The cap only exists so a
# pathologically small thumbnail is letterboxed rather than smeared across the
# whole card.
MAX_UPSCALE = 2.4

# How much of the card the photo fills. The hero keeps a panel deep enough for
# the price, the place and the size; the later slides ask for the whole card and
# get as much of it as the photo can afford at MAX_UPSCALE — which for the usual
# 600px landscape photo means a tall band and a slim price bar under it, and for
# a big portrait photo means true full bleed.
HERO_BAND = 0.62
PHOTO_BAND = 1.0


def _black(size):
    return _font("Montserrat-Black.ttf", size)


def trim_white_borders(im):
    """Crop the near-white borders some SUUMO photos carry.

    Lives here rather than inside the reel builder because both pipelines want
    it: the padding comes from the source listing and from our own fixed-box
    resizes, and it shows as white bands around an otherwise full-bleed card.
    """
    rgb = im.convert("RGB")
    bg = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, bg)
    # Amplify + offset so anything brighter than ~235 counts as "white".
    diff = ImageChops.add(diff, diff, 2.0, -20)
    bbox = diff.getbbox()
    # Only crop if there's a real border (ignore tiny/no-op or full-white).
    if bbox and bbox != (0, 0, rgb.width, rgb.height):
        cropped = im.crop(bbox)
        if cropped.width >= 50 and cropped.height >= 50:
            return cropped
    return im


def looks_like_a_drawing(path, paper_min):
    """True when the image is a plan or elevation rather than a photograph.

    Listings carry 間取り floor plans and 立面図 elevations among the photos, and
    a line drawing between two pictures of a house is what stops a scroll for
    the wrong reason.

    Position is the obvious test and it does not work. Across 96 images from 20
    listings the drawings turned up at positions 1 through 5, and the second
    photo — the one they were expected to be — was a photograph in every
    listing but one.

    "Mostly white" does not work either, and fails in the worst direction: a
    white house against a snowy street and a white sky measured 45% white, and
    it was a listing's first photo.

    What separates them is *paper* white — flat, exactly neutral, the same
    value across the whole background. Sky and snow are near-white but they
    shade, and JPEG leaves them a point or two off neutral. Measured over those
    96 images: 12 drawings scored 0.40-0.79 on this, every photograph scored
    0.12 or less, and the threshold sits in that gap. No false positives, and
    nothing missed.

    Cheap: the image is already on disk by the time this is asked, and it is
    read at 160x120.
    """
    try:
        with Image.open(path) as raw:
            raw.draft("RGB", (160, 120))
            small = raw.convert("RGB").resize((160, 120), Image.NEAREST)
            pixels = list(small.getdata())
    except Exception as exc:
        # An unreadable file is somebody else's problem — the renderer already
        # skips those, and guessing here would drop a photo for the wrong reason.
        logger.warning("Could not inspect %s: %s", path, exc)
        return False

    # NEAREST, not the default resample: averaging neighbours would invent
    # off-white pixels along every line in a plan and blur the thing being
    # measured.
    paper = sum(1 for r, g, b in pixels
                if min(r, g, b) >= 250 and max(r, g, b) - min(r, g, b) <= 3)
    return paper / len(pixels) > paper_min


def _photo_band(local_path, width, target_height):
    """The photo cover-cropped into a full-width band `target_height` tall.

    Falls short of the target rather than upscaling past MAX_UPSCALE, so the
    band a small photo produces is shallower and the panel below it deeper —
    which is why the type is laid out against a measured band and not a
    hardcoded y.

    `draft` first so a large JPEG is never fully decoded: the cheap thing, as
    everywhere else on this box that touches an image.
    """
    with Image.open(local_path) as raw:
        raw.draft("RGB", (width, target_height))
        photo = trim_white_borders(raw.convert("RGB"))

    scale = min(
        max(width / photo.width, target_height / photo.height), MAX_UPSCALE
    )
    photo = photo.resize(
        (max(1, round(photo.width * scale)), max(1, round(photo.height * scale))),
        Image.LANCZOS,
    )

    height = min(photo.height, target_height)
    band = Image.new("RGB", (width, height), BG)
    left = max(0, (photo.width - width) // 2)
    # Take the crop off the bottom rather than the middle: the foreground of a
    # listing photo is a parking space or a tatami floor, the middle is the
    # thing being sold.
    top = max(0, min((photo.height - height) // 3, photo.height - height))
    crop = photo.crop((
        left, top,
        left + min(width, photo.width), top + min(height, photo.height),
    ))
    band.paste(crop, ((width - crop.width) // 2, (height - crop.height) // 2))
    return band


def _scrim(img, top, bottom, strength=232):
    """Fade a dark gradient up from `bottom` to `top`. Legibility, not mood.

    Text on a photo is readable until the one listing whose front elevation is a
    white wall in full sun, and then it is invisible. A gradient costs nothing
    and removes the whole class of problem.
    """
    height = bottom - top
    if height <= 0:
        return
    ramp = Image.new("L", (1, height))
    ramp.putdata(
        [int(strength * (i / max(1, height - 1)) ** 1.1) for i in range(height)]
    )
    img.paste(
        Image.new("RGB", (img.width, height), BG),
        (0, top),
        ramp.resize((img.width, height), Image.BILINEAR),
    )


def _top_scrim(img, height=210, strength=170):
    """The mirror of `_scrim`, so the brand mark has something to sit on."""
    ramp = Image.new("L", (1, height))
    ramp.putdata(
        [int(strength * (1 - i / max(1, height - 1)) ** 1.4) for i in range(height)]
    )
    img.paste(
        Image.new("RGB", (img.width, height), BG),
        (0, 0),
        ramp.resize((img.width, height), Image.BILINEAR),
    )


def _photo_card(local_path, page, band_fraction):
    """A photo laid onto a card, plus the chrome every slide shares.

    Returns (image, draw, panel_top, full_bleed): where the type may start, and
    whether it will be sitting on a gradient over the photo rather than on a
    panel below it.
    """
    band = _photo_band(local_path, CARD_WIDTH, int(CARD_HEIGHT * band_fraction))
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG)
    img.paste(band, (0, 0))

    full_bleed = band.height >= CARD_HEIGHT - 4
    if full_bleed:
        _scrim(img, int(CARD_HEIGHT * 0.46), CARD_HEIGHT)
        panel_top = int(CARD_HEIGHT * 0.55)
    else:
        panel_top = band.height
        # A hairline where the photo meets the panel, so a dark photo doesn't
        # dissolve into the card.
        ImageDraw.Draw(img).line(
            [(0, band.height), (CARD_WIDTH, band.height)], fill=(38, 38, 38), width=2
        )

    draw = ImageDraw.Draw(img)
    if page == 0:
        # The top scrim exists for the wordmark. On the later slides there is
        # nothing up there, and darkening a clear sky for no reason is a cost
        # with no benefit.
        _top_scrim(img)
        # Wordmark on slide one only: repeating it over every photo is what
        # makes a carousel look stamped rather than designed, and the later
        # slides carry it on their price line anyway. White, not brass — brass
        # over a bright sky is the one place this palette stops being readable.
        draw.text((MARGIN, MARGIN - 6), BRAND.upper(), font=_bold(32), fill=FG)
        rule_y = MARGIN + 44
        draw.line([(MARGIN, rule_y), (MARGIN + 96, rule_y)], fill=ACCENT, width=4)
    return img, draw, panel_top, full_bleed


def _dots(draw, page, total):
    if total <= 1:
        return
    dot_r, gap = 7, 26
    total_w = total * dot_r * 2 + (total - 1) * (gap - dot_r * 2)
    x = CARD_WIDTH - MARGIN - total_w
    y = CARD_HEIGHT - MARGIN + 6
    for i in range(total):
        draw.ellipse([x, y, x + dot_r * 2, y + dot_r * 2],
                     fill=FG if i == page else (110, 110, 110))
        x += gap


def _short_area(value):
    """'101.24 m² (30.62 tsubo)' -> '101 m²'. Rounded, and the tsubo dropped.

    The caption has room for both units and two decimal places. A card does not,
    and the decimals are false precision on a scraped figure anyway — what the
    reader is doing with this number is deciding whether the house is big.
    """
    text = re.sub(r"\s*[（(][^)）]*[)）]", "", str(value or "")).strip()
    # The thousands separator has to be part of the match: '1,074 m²' read as
    # digits-and-dots alone stops at the comma and puts "Land 1 m²" on the card.
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return ""
    try:
        number = f"{round(float(match.group().replace(',', ''))):,}"
    except ValueError:
        return text
    unit = "m²" if ("m²" in text or "㎡" in text) else ""
    return f"{number} {unit}".strip()


def _details_line(building_area, land_area):
    parts = []
    building, land = _short_area(building_area), _short_area(land_area)
    if building:
        parts.append(f"Building {building}")
    if land:
        parts.append(f"Land {land}")
    return "   ·   ".join(parts)


def _stack(draw, price, location, details, max_width):
    """The type block, measured before it is drawn.

    Measured first because where it goes depends on how tall it turns out: over
    the bottom of a full-bleed photo, or centred in the panel under a landscape
    one. Returns [(lines, font, spacing, fill, gap_after)] and its total height.
    """
    blocks = []
    if price:
        font, lines, spacing = _fit(
            draw, price, _black, max_width, 320, (126, 114, 102, 90, 78)
        )
        blocks.append([lines, font, spacing, FG, 22])
    if location:
        font, lines, spacing = _fit(
            draw, location, _light, max_width, 140, (46, 42, 38, 34)
        )
        blocks.append([lines[:2], font, spacing, FG, 30])
    if details:
        font, lines, spacing = _fit(
            draw, details, _light, max_width, 46, (32, 30, 28, 26)
        )
        blocks.append([lines[:2], font, spacing, ACCENT, 0])
    if blocks:
        blocks[-1][4] = 0
    return blocks, _stack_height(blocks)


def _draw_stack(draw, blocks, y):
    for lines, font, spacing, fill, gap in blocks:
        y = _draw_lines(draw, lines, font, MARGIN, y, spacing, fill) + gap
    return y


def _stack_height(blocks):
    return sum(_block_height(b[0], b[1], b[2]) + b[4] for b in blocks)


def _place(panel_top, height, full_bleed):
    """Where the type block starts.

    Pinned to the floor when it is sitting on the photo — that is where the
    gradient is darkest — and centred when it has a panel of its own, because a
    two-line block anchored to the bottom of a deep panel leaves a hole above it
    that reads as a rendering fault.
    """
    bottom = CARD_HEIGHT - MARGIN - 40
    if full_bleed or panel_top + height + 56 > bottom:
        return bottom - height
    return panel_top + max(56, (bottom - panel_top - height) // 2)


def _hero(local_path, price, location, details, page, total, out_path):
    """Slide one: what it costs, where it is, how big — under or over the photo."""
    img, draw, panel_top, full_bleed = _photo_card(local_path, page, HERO_BAND)
    blocks, height = _stack(draw, price, location, details, CARD_WIDTH - MARGIN * 2)
    _draw_stack(draw, blocks, _place(panel_top, height, full_bleed))

    _dots(draw, page, total)
    img.save(out_path, "JPEG", quality=88, optimize=True)
    return out_path


def _photo(local_path, price, page, total, out_path):
    """A later photo: one line with the price and the wordmark, nothing else.

    The full hero treatment on every slide is what makes a carousel look like a
    template. But a photo pulled out of the carousel and reshared still has to
    say what it costs and who is selling it, so the line stays on all of them.
    """
    img, draw, panel_top, full_bleed = _photo_card(local_path, page, PHOTO_BAND)

    blocks = []
    if price:
        blocks.append([[price], _bold(54), 70, FG, 18])
    blocks.append([[BRAND], _light(30), 40, FG if full_bleed else MUTED, 0])
    _draw_stack(draw, blocks, _place(panel_top, _stack_height(blocks), full_bleed))

    _dots(draw, page, total)
    img.save(out_path, "JPEG", quality=88, optimize=True)
    return out_path


def _summary(price, location, details, link, page, total, out_path):
    """The closing slide: the facts once more, and where to go for the rest.

    A carousel that ends on a photo ends on nothing. This is what people are
    looking at when they decide whether to open the link, so it repeats the
    price rather than assuming they scrolled back for it.
    """
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    max_width = CARD_WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN - 6), "THE DETAILS", font=_bold(32), fill=ACCENT)
    draw.line([(MARGIN, MARGIN + 44), (MARGIN + 96, MARGIN + 44)],
              fill=ACCENT, width=4)

    blocks, height = _stack(draw, price, location, details, max_width)
    top, floor = MARGIN + 180, CARD_HEIGHT - MARGIN - 260
    _draw_stack(draw, blocks, top + max(0, (floor - top - height) // 2))

    cta_y = CARD_HEIGHT - MARGIN - 190
    draw.line([(MARGIN, cta_y), (CARD_WIDTH - MARGIN, cta_y)],
              fill=(58, 58, 58), width=2)
    draw.text((MARGIN, cta_y + 40), "SEE THE FULL LISTING",
              font=_bold(36), fill=ACCENT)
    if link:
        link_font = _light(32)
        for i, line in enumerate(_wrap(draw, link, link_font, max_width)[:2]):
            draw.text((MARGIN, cta_y + 96 + i * 42), line,
                      font=link_font, fill=FG)

    _dots(draw, page, total)
    img.save(out_path, "JPEG", quality=88, optimize=True)
    return out_path


def prune_old_cards(directory, prefix, keep_days):
    """Delete cards we rendered more than `keep_days` ago.

    Five JPEGs per listing post, twice a day, in two directories (one to draw
    into, one for whitenoise to serve) is most of a gigabyte a year on a box
    that does not have one to spare. Instagram has fetched the card long before
    this runs, and the post keeps its own record in SocialPost either way.

    Only files starting with `prefix` are touched, so the text-card pipeline's
    output — which admin drafts still link to — is left alone.
    """
    if keep_days <= 0 or not os.path.isdir(directory):
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for name in os.listdir(directory):
        if not name.startswith(prefix):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as exc:
            logger.warning("Could not prune %s: %s", path, exc)
    if removed:
        logger.info("Pruned %s card(s) older than %s days from %s",
                    removed, keep_days, directory)
    return removed


def render_listing_cards(
    photo_paths,
    price,
    location,
    building_area="",
    land_area="",
    link="",
    out_dir=None,
    slug="listing",
    add_summary=True,
):
    """Render a listing carousel from already-downloaded photos.

    Takes local paths rather than URLs so the caller keeps the decisions about
    downloading, retries and what a 404 means — a photo that will not load is a
    fact about the listing, not about the layout.

    Returns paths in carousel order. A photo that cannot be drawn is skipped
    rather than fatal: three good slides beat none.
    """
    os.makedirs(out_dir, exist_ok=True)
    price = str(price or "").strip()
    details = _details_line(building_area, land_area)

    # Drop unreadable files before counting: the page dots are drawn onto each
    # card, so the total has to be right before the first one is saved. Opening
    # only reads the header, which is enough to catch the realistic failure —
    # a "download" that is actually an error page.
    usable = []
    for path in photo_paths:
        try:
            with Image.open(path):
                usable.append(path)
        except Exception as exc:
            logger.warning("Not a usable image, skipping %s: %s", path, exc)

    photo_paths = usable
    total = len(photo_paths) + (1 if add_summary and photo_paths else 0)

    paths, page = [], 0
    for local_path in photo_paths:
        out_path = os.path.join(out_dir, f"{slug}-{page + 1}.jpg")
        try:
            if page == 0:
                _hero(local_path, price, location, details, page, total, out_path)
            else:
                _photo(local_path, price, page, total, out_path)
        except Exception as exc:
            logger.warning("Could not draw a card for %s: %s", local_path, exc)
            continue
        paths.append(out_path)
        page += 1

    if paths and add_summary:
        out_path = os.path.join(out_dir, f"{slug}-{page + 1}.jpg")
        try:
            _summary(price, location, details, link, page, total, out_path)
            paths.append(out_path)
        except Exception as exc:
            logger.warning("Could not draw the summary card: %s", exc)

    return paths
