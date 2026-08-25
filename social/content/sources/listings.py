"""The listings themselves, kept in the rotation rather than rewritten.

There is already a working reel pipeline that picks a property, builds the
video, writes the caption and posts it. The planner does not need to know how
any of that happens — it needs to know that "post a house" is one of the things
it can choose today, and how much weight to give it. So this source yields a
single marker material and the publisher hands it back to the existing code.
"""

import logging

from social.content.material import Material
from social.models import SocialPost
from social.utils import select_properties_to_post

logger = logging.getLogger(__name__)


def gather():
    # Only offer a listing slot if the existing queue actually has something to
    # post; otherwise the planner would keep choosing a slot that does nothing.
    from social.constants import PRICE_LIMIT_INSTAGRAM

    reels = SocialPost.objects.filter(
        social_media="instagram", content_type="reel"
    )
    try:
        candidates = select_properties_to_post(reels, PRICE_LIMIT_INSTAGRAM, 1)
    except Exception as exc:
        logger.warning("Could not check the listing queue: %s", exc)
        return []

    if not candidates:
        logger.info("Listings: nothing in the queue")
        return []

    return [
        Material(
            kind=SocialPost.KIND_LISTING,
            key="listing:next-in-queue",
            headline="Next house in the queue",
            facts=[],  # the existing pipeline writes its own copy
            medium="reel",
            cooldown_days=None,
        )
    ]
