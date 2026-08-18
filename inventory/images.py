"""Resized versions of the listing photos we link to.

Listing photos are not ours: `PropertyImage.file` holds a full external URL on
suumo.jp or homes.jp, at whatever size that site published — routinely 600-1000px
and 130-250KB of JPEG. We display them far smaller than that.

Measuring the home page on a throttled 4G phone put images at 74% of all bytes
and LCP at 23s, which is the number that decides whether a visitor waits. Rather
than storing our own copies (a 10k-property backfill on a low-RAM box), the URL
is routed through a proxy that fetches, resizes and re-encodes on demand.

WHAT THE MEASUREMENTS SAY. On a 1000x750 source that ships as 252KB of JPEG:

    requested   q=80    q=70    q=60
    600w        105KB    88KB    80KB
    1000w       270KB   228KB   210KB
    1400w       270KB   228KB   210KB     (`we` caps at the source's 1000px)

Two things follow, and both matter more than they look:

  1. The saving is in the DOWNSCALE, not the codec. At 600w we win 58%; at the
     source's own width the best case is 17%.
  2. Re-encoding at native size can make a file BIGGER — 270KB against a 252KB
     original. These source JPEGs are already well compressed, so asking for
     WebP at a width the source cannot fill is a straight loss.

So: never request a width at or above the source's, and size each request to
what is actually on screen. Widths below come from measured display sizes at a
1440px viewport, roughly 2x for a 2x screen.

    card      290x217  -> 600    home grid, map list, related properties
    gallery  1010x540  -> 1000   the property page carousel (600 on phones)
"""

from urllib.parse import quote

PROXY = "https://wsrv.nl/"

WIDTH_CARD = 600
WIDTH_GALLERY = 1000

# Cards are downscaled a long way, so they can afford a higher quality and still
# come out far smaller. The gallery is served near the source's own width, where
# q=80 would inflate the file past the original — see the table above.
QUALITY_CARD = 75
QUALITY_GALLERY = 70


def thumb_url(url, width=WIDTH_CARD, quality=QUALITY_CARD):
    """A resized WebP version of a remote image URL.

    Anything that is not a remote http(s) URL is returned unchanged, so local
    static paths, empty values and None pass straight through.

    `we` (without enlargement) means a request wider than the source returns the
    source's own dimensions instead of upscaling it — it never makes an image
    blurrier to save bytes. Note the corollary from the table above: because the
    proxy caps at the source width, a too-wide request does not fail loudly, it
    just quietly returns a re-encode that may be larger than the original.
    """
    if not url:
        return url
    url = str(url)
    if not url.startswith(("http://", "https://")):
        return url

    def _int(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    width = _int(width, WIDTH_CARD)
    quality = _int(quality, QUALITY_CARD)
    return f"{PROXY}?url={quote(url, safe='')}&w={width}&output=webp&q={quality}&we"
