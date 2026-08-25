"""The questions, from the curated bank and eventually from the comments."""

import logging
import re

from social.content.faq_bank import FAQ_BANK, FAQ_BY_KEY, FAQ_COOLDOWN_DAYS
from social.content.material import Material
from social.models import SocialComment, SocialPost

logger = logging.getLogger(__name__)


def gather():
    materials = []
    for entry in FAQ_BANK:
        materials.append(
            Material(
                kind=SocialPost.KIND_FAQ,
                key=f"faq:{entry['key']}",
                headline=entry["question"],
                facts=entry["facts"],
                medium="carousel",
                eyebrow="FAQ",
                body_eyebrow="The answer",
                cooldown_days=FAQ_COOLDOWN_DAYS,
                needs_review=bool(entry.get("needs_review")),
            )
        )
    logger.info("FAQ: %s bank entries", len(materials))
    return materials


def unanswered_follower_questions():
    """Questions people actually asked that the bank has no facts for.

    Deliberately not turned into posts: an answer needs facts, and inventing
    them is the one thing this pipeline exists to prevent. Reported instead, so
    the bank can be extended by hand — which is how it stops being a guess about
    what people ask and becomes a record of it.
    """
    asked = (
        SocialComment.objects.exclude(question="")
        .values_list("question", flat=True)
        .order_by("-datetime")[:200]
    )
    bank_words = {
        key: set(re.findall(r"[a-z]{4,}", entry["question"].lower()))
        for key, entry in FAQ_BY_KEY.items()
    }
    unmatched = []
    for question in asked:
        words = set(re.findall(r"[a-z]{4,}", question.lower()))
        if words and not any(len(words & bank) >= 2 for bank in bank_words.values()):
            unmatched.append(question)
    return unmatched
