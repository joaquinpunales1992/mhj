"""Renders the text cards that non-listing content is made of.

PIL rather than moviepy: a still card costs a few megabytes, where the reel
builder has already been OOM-killed on this VPS at higher resolutions. Same
reason the reels are 540x960 — do the cheap thing.

Feed cards are 1080x1350 (4:5), the tallest still Instagram shows without
cropping. Stories are 1080x1920.
"""

import logging
import os

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CARD_WIDTH = 1080
CARD_HEIGHT = 1350
STORY_WIDTH = 1080
STORY_HEIGHT = 1920

BG = (17, 17, 17)          # the reels' backdrop, so the grid reads as one account
FG = (245, 245, 245)
MUTED = (150, 150, 150)
ACCENT = (214, 168, 90)    # warm brass; reads on dark without shouting

MARGIN = 90
BRAND = "akiyainjapan.com"


def _font(name, size):
    """Load a Montserrat face, falling back to PIL's default.

    A missing font file must not be what stops a post going out — an ugly card
    is recoverable, a cron job that died at 3am is not noticed for a week.
    """
    try:
        return ImageFont.truetype(
            os.path.join(settings.STATIC_ROOT, "fonts", name), size
        )
    except Exception as exc:
        logger.warning("Font %s unavailable (%s); using default", name, exc)
        return ImageFont.load_default(size=size)


def _bold(size):
    return _font("Montserrat-Bold.ttf", size)


def _light(size):
    return _font("Montserrat-Light.ttf", size)


def _block_height(lines, font, spacing):
    if not lines:
        return 0
    ascent, descent = font.getmetrics()
    return (len(lines) - 1) * spacing + ascent + descent


def _wrap(draw, text, font, max_width):
    """Greedy wrap on width in pixels, not characters.

    textwrap alone is wrong here: 'Will renovation' and 'MMMMMMMMMMMMMMM' are
    the same number of characters and nothing like the same width.
    """
    words = text.split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit(draw, text, font_factory, max_width, max_height, sizes):
    """Largest size from `sizes` whose wrapped text fits the box.

    Falls back to the smallest and lets it overflow rather than raising — a
    cramped card still says the thing.
    """
    for size in sizes:
        font = font_factory(size)
        spacing = int(size * 1.34)
        lines = _wrap(draw, text, font, max_width)
        if _block_height(lines, font, spacing) <= max_height:
            return font, lines, spacing
    font = font_factory(sizes[-1])
    return font, _wrap(draw, text, font, max_width), int(sizes[-1] * 1.34)


def _draw_lines(draw, lines, font, x, y, spacing, fill):
    ascent, descent = font.getmetrics()
    for i, line in enumerate(lines):
        draw.text((x, y + i * spacing), line, font=font, fill=fill)
    return y + (len(lines) - 1) * spacing + ascent + descent


def _chrome(draw, eyebrow, page, total, width, height, footnote=""):
    """What every card shares: eyebrow, rule, brand, page dots, footnote."""
    draw.text((MARGIN, MARGIN), eyebrow.upper(), font=_bold(34), fill=ACCENT)
    rule_y = MARGIN + 62
    draw.line([(MARGIN, rule_y), (MARGIN + 120, rule_y)], fill=ACCENT, width=5)

    brand_y = height - MARGIN - 32
    draw.text((MARGIN, brand_y), BRAND, font=_light(32), fill=MUTED)

    if footnote:
        # Above the brand line, in muted type: this is where a news item's
        # outlet goes, so attribution travels with the image and not only with
        # the caption people may never expand.
        note_font = _light(28)
        for i, line in enumerate(
            _wrap(draw, footnote, note_font, width - MARGIN * 2)[:2]
        ):
            draw.text(
                (MARGIN, brand_y - 46 - (1 - i) * 34), line,
                font=note_font, fill=MUTED,
            )

    if total > 1:
        dot_r, gap = 7, 26
        total_w = total * dot_r * 2 + (total - 1) * (gap - dot_r * 2)
        x = width - MARGIN - total_w
        y = height - MARGIN - 20
        for i in range(total):
            draw.ellipse(
                [x, y, x + dot_r * 2, y + dot_r * 2],
                fill=FG if i == page else (70, 70, 70),
            )
            x += gap


def _canvas(eyebrow, page, total, width, height, footnote=""):
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    _chrome(draw, eyebrow, page, total, width, height, footnote)
    return img, draw


def _body_box(height, footnote=False):
    """The space between the chrome above and the brand line below."""
    top = MARGIN + 150
    bottom = height - MARGIN - (170 if footnote else 110)
    return top, bottom - top


def _paginate(draw, paragraphs, font_size, max_width, max_height):
    """Split body text across cards, breaking only between paragraphs.

    Shrinking type until three facts fit one card is how these end up
    unreadable on a phone; adding a card costs nothing.
    """
    font = _light(font_size)
    spacing = int(font_size * 1.46)
    para_gap = int(font_size * 0.72)

    pages, current, current_h = [], [], 0
    for para in paragraphs:
        lines = _wrap(draw, para, font, max_width)
        h = _block_height(lines, font, spacing)
        extra = h + (para_gap if current else 0)
        if current and current_h + extra > max_height:
            pages.append(current)
            current, current_h = [lines], h
        else:
            current.append(lines)
            current_h += extra
    if current:
        pages.append(current)
    return pages, font, spacing, para_gap


def render_cards(
    headline,
    body,
    out_dir,
    slug,
    eyebrow="",
    body_eyebrow="",
    swipe_hint="",
    footnote="",
    max_body_cards=2,
):
    """Render a carousel: a headline card, then the body across 1-2 cards.

    The headline gets its own card because that is what has to stop the scroll.
    Returns paths in carousel order.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    max_width = CARD_WIDTH - MARGIN * 2
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    body_top, body_height = _body_box(CARD_HEIGHT, footnote=bool(footnote))
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]

    # Lay the body out first: it decides how many cards there are, and the
    # headline card's page dots need that count.
    pages = None
    for size in (54, 50, 46, 42, 38):
        candidate, b_font, b_spacing, b_gap = _paginate(
            probe, paragraphs, size, max_width, body_height
        )
        if len(candidate) <= max_body_cards:
            pages = candidate
            break
    if pages is None:
        pages = candidate

    total = 1 + len(pages)

    head_top, head_height = _body_box(CARD_HEIGHT, footnote=bool(footnote))
    img, draw = _canvas(eyebrow, 0, total, CARD_WIDTH, CARD_HEIGHT, footnote)
    h_font, h_lines, h_spacing = _fit(
        draw, headline, _bold, max_width, head_height, (86, 78, 70, 62, 56, 50)
    )
    # Centred in its box rather than pinned to the top: a short headline pinned
    # high leaves the card looking unfinished.
    offset = max(0, (head_height - _block_height(h_lines, h_font, h_spacing)) // 2)
    _draw_lines(draw, h_lines, h_font, MARGIN, head_top + offset, h_spacing, FG)
    if swipe_hint:
        draw.text(
            (MARGIN, CARD_HEIGHT - MARGIN - 78), swipe_hint,
            font=_light(30), fill=ACCENT,
        )
    path = os.path.join(out_dir, f"{slug}-1.jpg")
    img.save(path, "JPEG", quality=88, optimize=True)
    paths.append(path)

    for index, page in enumerate(pages):
        label = body_eyebrow or eyebrow
        if index:
            label = f"{label}, cont."
        img, draw = _canvas(label, index + 1, total, CARD_WIDTH, CARD_HEIGHT,
                            footnote)
        y = body_top
        for para_lines in page:
            y = _draw_lines(draw, para_lines, b_font, MARGIN, y, b_spacing, FG)
            y += b_gap
        path = os.path.join(out_dir, f"{slug}-{index + 2}.jpg")
        img.save(path, "JPEG", quality=88, optimize=True)
        paths.append(path)

    return paths


def render_single_card(headline, body, out_dir, slug, eyebrow="", footnote=""):
    """One 4:5 card: a big headline, a short body under it. For stats posts.

    The pair is centred as a group in the body box. Pinned to the top, a
    three-line stat left the bottom half of the card empty and the whole thing
    looked like a rendering bug.
    """
    os.makedirs(out_dir, exist_ok=True)
    max_width = CARD_WIDTH - MARGIN * 2
    body_top, body_height = _body_box(CARD_HEIGHT, footnote=bool(footnote))

    img, draw = _canvas(eyebrow, 0, 1, CARD_WIDTH, CARD_HEIGHT, footnote)
    h_font, h_lines, h_spacing = _fit(
        draw, headline, _bold, max_width, int(body_height * 0.45),
        (108, 96, 84, 74, 64),
    )
    head_h = _block_height(h_lines, h_font, h_spacing)

    gap = 56
    paragraphs = [p.strip() for p in (body or "").split("\n") if p.strip()]
    b_font = b_spacing = None
    para_lines, body_h, para_gap = [], 0, 0
    if paragraphs:
        remaining = body_height - head_h - gap
        for size in (48, 44, 40, 36, 32):
            b_font = _light(size)
            b_spacing = int(size * 1.42)
            para_gap = int(size * 0.6)
            para_lines = [_wrap(draw, p, b_font, max_width) for p in paragraphs]
            body_h = sum(
                _block_height(lines, b_font, b_spacing) for lines in para_lines
            ) + para_gap * (len(para_lines) - 1)
            if body_h <= remaining:
                break

    total_h = head_h + (gap + body_h if para_lines else 0)
    y = body_top + max(0, (body_height - total_h) // 2)

    y = _draw_lines(draw, h_lines, h_font, MARGIN, y, h_spacing, FG)
    if para_lines:
        y += gap
        for lines in para_lines:
            y = _draw_lines(draw, lines, b_font, MARGIN, y, b_spacing, MUTED)
            y += para_gap

    path = os.path.join(out_dir, f"{slug}.jpg")
    img.save(path, "JPEG", quality=88, optimize=True)
    return [path]


def render_story_card(headline, body, out_dir, slug, eyebrow=""):
    """One 9:16 story card. Text sits in the middle third.

    Stories are tapped through in under two seconds and the top and bottom of
    the screen are covered by Instagram's own chrome, so nothing important goes
    there.
    """
    os.makedirs(out_dir, exist_ok=True)
    max_width = STORY_WIDTH - MARGIN * 2
    img, draw = _canvas(eyebrow, 0, 1, STORY_WIDTH, STORY_HEIGHT)

    box_top = int(STORY_HEIGHT * 0.30)
    box_height = int(STORY_HEIGHT * 0.40)
    h_font, h_lines, h_spacing = _fit(
        draw, headline, _bold, max_width, int(box_height * 0.6),
        (96, 86, 76, 66, 58),
    )
    y = _draw_lines(draw, h_lines, h_font, MARGIN, box_top, h_spacing, FG)
    if body:
        b_font, b_lines, b_spacing = _fit(
            draw, body, _light, max_width, box_height - (y - box_top) - 50,
            (46, 42, 38),
        )
        _draw_lines(draw, b_lines, b_font, MARGIN, y + 50, b_spacing, MUTED)

    path = os.path.join(out_dir, f"{slug}-story.jpg")
    img.save(path, "JPEG", quality=88, optimize=True)
    return [path]
