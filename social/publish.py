"""Publishing primitives: put a caption and some media on a network.

Everything the poster did until now assumed a post *was* a Property — the Graph
API calls lived inside post_to_instagram(property, ...), so there was nowhere to
put a post that isn't a listing. These functions know about images and captions
and nothing else, which is what lets FAQ cards, stats cards and listings all go
out through the same door.

They return the published id (the thing insights are later read back with) or
None, and they do not touch the database: the caller decides what to record,
because what a listing post needs recorded is not what an FAQ post needs.
"""

import logging

import requests

from social.constants import GRAPH_API_VERSION, INSTAGRAM_USER_ID, PAGE_ID
from social.utils import get_fresh_token

logger = logging.getLogger(__name__)

GRAPH = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Instagram fetches the image itself, and a cold cache on our side plus its
# fetch means this is not instant.
TIMEOUT = 60


def _ig_container(payload):
    response = requests.post(f"{GRAPH}/{INSTAGRAM_USER_ID}/media", data=payload,
                             timeout=TIMEOUT)
    result = response.json()
    if "id" in result:
        return result["id"]
    logger.error("Instagram container failed: %s", result)
    return None


def _ig_publish(creation_id):
    response = requests.post(
        f"{GRAPH}/{INSTAGRAM_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": get_fresh_token()},
        timeout=TIMEOUT,
    )
    if response.status_code == 200:
        return str(response.json().get("id") or "")
    logger.error("Instagram publish failed: %s", response.text)
    return None


def publish_instagram_photos(image_urls, caption):
    """Publish one image, or a carousel when given several.

    The single-image case is deliberately not a one-item carousel: Instagram
    rejects a carousel with fewer than two children, which is exactly what a
    one-card post would be.
    """
    image_urls = [u for u in image_urls if u]
    if not image_urls:
        logger.warning("No image URLs given; skipping Instagram post.")
        return None

    if len(image_urls) == 1:
        creation_id = _ig_container({
            "image_url": image_urls[0],
            "caption": caption,
            "access_token": get_fresh_token(),
        })
        return _ig_publish(creation_id) if creation_id else None

    children = []
    for url in image_urls:
        child = _ig_container({
            "image_url": url,
            "is_carousel_item": True,
            "access_token": get_fresh_token(),
        })
        if child:
            children.append(child)

    # One surviving child cannot be a carousel, so post it on its own rather
    # than losing the whole post to a single failed upload.
    if len(children) == 1:
        return _ig_publish(children[0])
    if not children:
        logger.error("No carousel children uploaded; skipping Instagram post.")
        return None

    creation_id = _ig_container({
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": caption,
        "access_token": get_fresh_token(),
    })
    return _ig_publish(creation_id) if creation_id else None


def publish_facebook_photos(image_urls, caption):
    """Publish a Page post with one or more photos attached."""
    image_urls = [u for u in image_urls if u]
    if not image_urls:
        logger.warning("No image URLs given; skipping Facebook post.")
        return None

    media_fbids = []
    for url in image_urls:
        response = requests.post(
            f"{GRAPH}/{PAGE_ID}/photos",
            data={"url": url, "published": "false",
                  "access_token": get_fresh_token()},
            timeout=TIMEOUT,
        )
        result = response.json()
        if response.status_code == 200 and "id" in result:
            media_fbids.append(result["id"])
        else:
            logger.error("Failed to upload image to Facebook: %s", result)

    if not media_fbids:
        logger.error("No images uploaded; skipping Facebook post.")
        return None

    payload = {"message": caption, "access_token": get_fresh_token()}
    for i, media_id in enumerate(media_fbids):
        payload[f"attached_media[{i}]"] = f'{{"media_fbid":"{media_id}"}}'

    response = requests.post(f"{GRAPH}/{PAGE_ID}/feed", data=payload,
                             timeout=TIMEOUT)
    result = response.json()
    if response.status_code == 200:
        return str(result.get("id") or "")
    logger.error("Failed to create Facebook post: %s", result)
    return None


def publish_instagram_story(image_url):
    """Publish a still story.

    Stories are the same two-step container/publish flow as a feed post with
    media_type=STORIES, and they take no caption — nothing written is shown.

    What the API will *not* do is create a poll, a question box or a link
    sticker: those are app-only. So an automated story is an image and nothing
    more, and anything it needs to say has to be drawn onto the image.
    """
    creation_id = _ig_container({
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": get_fresh_token(),
    })
    return _ig_publish(creation_id) if creation_id else None
