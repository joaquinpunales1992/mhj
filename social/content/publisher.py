"""Turning a chosen material into a published post.

Knows about media and captions, not about where material came from: a news
story, a number out of the database and a question all reach this the same way,
which is what makes adding a format cheap.

Listings are the exception, and deliberately so — there is already a working
reel pipeline that picks a property, builds the video and posts it, and
rewriting that to fit a general interface would risk the one thing on this
account that already earns.
"""

import logging
import os

from django.conf import settings
from django.utils import timezone

from social.constants import SOCIAL_REQUIRE_APPROVAL
from social.content.cards import (
    render_cards,
    render_single_card,
    render_story_card,
)
from social.content.copy import build_caption, write_copy
from social.content.hosting import public_url_for_card
from social.models import ContentDraft, SocialPost
from social.publish import (
    publish_facebook_photos,
    publish_instagram_photos,
    publish_instagram_story,
)

logger = logging.getLogger(__name__)

CARD_DIR = os.path.join(settings.MEDIA_ROOT, "social_cards")


def _slug(material, medium):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    safe_key = material.key.replace(":", "-").replace("/", "-")[:60]
    return f"{safe_key}-{medium}-{stamp}"


def _render(material, medium, body):
    slug = _slug(material, medium)
    if medium == "story":
        # A story is glanced at, so it carries the headline and one line — the
        # first paragraph of the body, never all three.
        first = body.split("\n")[0]
        return render_story_card(
            material.headline, first, CARD_DIR, slug,
            eyebrow=material.eyebrow or "akiyainjapan.com",
        )
    if medium == "single":
        return render_single_card(
            material.headline, body, CARD_DIR, slug,
            eyebrow=material.eyebrow, footnote=material.footnote,
        )
    return render_cards(
        material.headline, body, CARD_DIR, slug,
        eyebrow=material.eyebrow,
        body_eyebrow=material.body_eyebrow,
        swipe_hint="swipe →",
        footnote=material.footnote,
    )


def _log(material, medium, body, caption, paths, status):
    return ContentDraft.objects.create(
        kind=material.kind,
        status=status,
        source=(
            ContentDraft.SOURCE_COMMENT
            if material.meta.get("from_comment")
            else ContentDraft.SOURCE_BANK
        ),
        key=material.key,
        question=material.headline[:500],
        answer=body,
        caption=caption,
        card_paths="\n".join(paths),
        needs_review=material.needs_review,
        posted_at=timezone.now() if status == ContentDraft.STATUS_POSTED else None,
    )


def _publish_listing(material):
    """Hand a listing slot back to the pipeline that already does this well."""
    from social.utils import post_instagram_reel

    result = post_instagram_reel()
    posted = bool(result) if result is not None else True
    logger.info("Listing slot delegated to the reel pipeline (posted=%s)", posted)
    return posted


def publish(material, medium, networks=("instagram",), dry_run=False):
    """Publish one material. Returns a dict describing what happened.

    Every failure path returns rather than raises, and records why: this runs
    from cron with nobody watching, and a run that says nothing today is a
    normal outcome. Saying the wrong thing is not.
    """
    outcome = {
        "material": str(material), "medium": medium, "posted": [],
        "skipped": "", "caption": "", "cards": [],
    }

    if material.kind == SocialPost.KIND_LISTING:
        if dry_run:
            outcome["skipped"] = "dry run — would post the next queued reel"
            return outcome
        if _publish_listing(material):
            outcome["posted"] = ["instagram"]
        else:
            outcome["skipped"] = "the reel pipeline posted nothing"
        return outcome

    try:
        body, caption_body = write_copy(material)
    except Exception as exc:
        # The copy could not be written inside the facts, so there is nothing
        # safe to say. Skipping is the correct outcome, not a fallback caption.
        logger.error("Skipping %s: %s", material, exc)
        outcome["skipped"] = f"no usable copy: {exc}"
        return outcome

    caption = build_caption(material, caption_body)
    try:
        paths = _render(material, medium, body)
    except Exception as exc:
        logger.error("Could not render cards for %s: %s", material, exc)
        outcome["skipped"] = f"render failed: {exc}"
        return outcome

    outcome["caption"] = caption
    outcome["cards"] = paths

    if dry_run:
        outcome["skipped"] = "dry run"
        return outcome

    if SOCIAL_REQUIRE_APPROVAL:
        draft = _log(material, medium, body, caption, paths,
                     ContentDraft.STATUS_DRAFT)
        outcome["skipped"] = f"held for approval as draft #{draft.pk}"
        return outcome

    urls = [url for url in (public_url_for_card(p) for p in paths) if url]
    if not urls:
        outcome["skipped"] = "no card could be made publicly fetchable"
        logger.error("%s: %s", material, outcome["skipped"])
        return outcome

    if medium == "story":
        media_id = publish_instagram_story(urls[0])
        if media_id:
            SocialPost.objects.create(
                post_kind=material.kind, caption=caption, ai_caption=body,
                caption_angle=material.key, media_id=media_id,
                social_media="instagram", content_type="story",
            )
            outcome["posted"] = ["instagram"]
    else:
        for network in networks:
            publisher = (
                publish_instagram_photos if network == "instagram"
                else publish_facebook_photos
            )
            # Facebook is the only one of the two where a link is clickable, so
            # it is the only one that gets the URL appended.
            network_caption = caption
            if network == "facebook" and material.link:
                network_caption = f"{caption}\n\n{material.link}"

            media_id = publisher(urls, network_caption)
            if not media_id:
                continue
            SocialPost.objects.create(
                post_kind=material.kind, caption=network_caption, ai_caption=body,
                caption_angle=material.key, media_id=media_id,
                social_media=network, content_type="post",
            )
            outcome["posted"].append(network)

    status = (
        ContentDraft.STATUS_POSTED if outcome["posted"]
        else ContentDraft.STATUS_DRAFT
    )
    _log(material, medium, body, caption, paths, status)
    if not outcome["posted"]:
        outcome["skipped"] = "every network refused the post — see the log"
    return outcome
