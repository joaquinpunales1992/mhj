"""Getting a locally rendered card to a URL Instagram can fetch.

The Graph API will not accept an upload: it takes an `image_url` and fetches it
itself. Property photos sidestep this because they are already public on the
source listing's host — a card we just drew is not.

So: copy it under STATIC_ROOT and serve it off our own domain. That is
whitenoise's job here and it works with DEBUG off, whereas MEDIA_ROOT is served
by the `static()` helper in urls.py, which returns nothing in production. If the
card turns out not to be reachable (fresh deploy, collectstatic not run, cache
in the way), fall back to the same public file hosts the reels already use.
"""

import logging
import os
import shutil

import requests
from django.conf import settings

from social.utils import _upload_video

logger = logging.getLogger(__name__)

CARD_SUBDIR = "social_cards"


def _public_base():
    # Trailing slashes here produce '...com//static/...', which some hosts 404.
    return getattr(
        settings, "SOCIAL_PUBLIC_BASE_URL", "https://www.akiyainjapan.com"
    ).rstrip("/")


def _reachable(url):
    try:
        response = requests.head(url, timeout=20, allow_redirects=True)
        if response.status_code == 200:
            return True
        logger.warning("Card URL %s returned %s", url, response.status_code)
    except Exception as exc:
        logger.warning("Card URL %s not reachable: %s", url, exc)
    return False


def public_url_for_card(local_path):
    """Return a publicly fetchable URL for a rendered card, or None.

    Copies rather than moves, so the draft's local copy stays where the admin
    and the --dry-run output expect to find it.
    """
    filename = os.path.basename(local_path)
    served_dir = os.path.join(settings.STATIC_ROOT, CARD_SUBDIR)
    os.makedirs(served_dir, exist_ok=True)
    served_path = os.path.join(served_dir, filename)

    if os.path.abspath(served_path) != os.path.abspath(local_path):
        shutil.copyfile(local_path, served_path)

    url = f"{_public_base()}/static/{CARD_SUBDIR}/{filename}"
    if _reachable(url):
        return url

    logger.info("Falling back to a public file host for %s", filename)
    return _upload_video(served_path)
