from social.constants import *
import logging
from ai.hugging import HuggingFaceAI
from ai.providers import ai_client
from social.models import SocialPost, SocialComment
from inventory.models import Property
from social.utils import (
    post_to_facebook,
    post_to_instagram,
    post_instagram_reel,
    get_fresh_token,
    select_properties_to_post,
)
from social.constants import INSTAGRAM_USER_ID
import requests

logger = logging.getLogger(__name__)


def post_instagram_reel():
    post_instagram_reel()


def post_on_facebook_batch(price_limit: int, batch_size: int):
    facebook_posts = SocialPost.objects.filter(social_media="facebook")

    properties_to_post_facebook = select_properties_to_post(
        facebook_posts, price_limit, batch_size
    )

    for property in properties_to_post_facebook:
        try:
            last_caption_generated = (
                facebook_posts.order_by("-datetime").first().ai_caption
                if facebook_posts
                else None
            )
            post_to_facebook(
                property=property,
                last_caption_generated=last_caption_generated,
                use_ai_caption=USE_AI_CAPTION,
            )
        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue


def post_on_instagram_batch(price_limit: int, batch_size: int):
    instagram_posts = SocialPost.objects.filter(
        social_media="instagram", content_type="post"
    )

    properties_to_post_instagram = select_properties_to_post(
        instagram_posts, price_limit, batch_size
    )

    for property in properties_to_post_instagram:
        try:
            last_caption_generated = (
                instagram_posts.order_by("-datetime").first().ai_caption
                if instagram_posts
                else None
            )

            post_to_instagram(
                property=property,
                last_caption_generated=last_caption_generated,
                use_ai_caption=USE_AI_CAPTION,
            )

        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue


def _reply_comment(comment_id: int, reply_message: str):
    url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
    payload = {
        "message": reply_message,
        "access_token": get_fresh_token(),
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        logger.info("Replied successfully!")
        return response.json()
    else:
        # logger takes %-style args, not print's comma list: the old form logged
        # the bare string and swallowed the reason the reply failed.
        logger.error("Error replying: %s", response.text)
        return None


def _get_reels():
    url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
    params = {
        "fields": "id,caption,media_type",
        "access_token": get_fresh_token(),
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    media = response.json()["data"]
    # Filter Reels
    return [item for item in media if item["media_type"] == "VIDEO"]


def _get_comments_per_reel(media_id):
    url = f"https://graph.facebook.com/v19.0/{media_id}/comments"
    params = {
        "access_token": get_fresh_token(),
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json().get("data", [])


def _reply_comments_instagram_post():
    pass


def _reply_comments_instagram_reels():
    from django.db.models import Q

    reels = _get_reels()
    for reel in reels:
        comments = _get_comments_per_reel(reel["id"])
        reel_id = reel["id"]
        replied_social_comments_ids_per_reel = SocialComment.objects.filter(
            Q(post=reel_id, replied=True) | Q(self_comment=True)
        ).values_list("comment_id", flat=True)

        for comment in comments:
            comment_id = comment["id"]
            comment = comment["text"]

            if (
                int(comment_id) in replied_social_comments_ids_per_reel
                or comment == DEFAULT_COMMENT
            ):
                continue

            llm = ai_client()
            # The comment itself now goes into the prompt. It never did before,
            # so every reply was the same generic "check our bio" regardless of
            # what was asked — which reads as a bot and ends the conversation.
            # A reply that answers the question gets answered back, and that
            # exchange is worth more to the reel's reach than the reply is.
            ai_comment = llm.generate_text(
                prompt=(
                    "You reply to comments as the owner of an account that lists "
                    "cheap houses (akiya) in Japan for international buyers.\n\n"
                    f"Their comment: {comment}\n\n"
                    "Write ONE short reply, at most 200 characters:\n"
                    "- Answer what they actually said. If it is a question you "
                    "cannot answer from the comment alone, say what it depends on "
                    "and invite them to ask.\n"
                    "- Only mention the link in our bio when they are asking where "
                    "to see the listing, the price or more photos.\n"
                    "- Warm and plain-spoken. At most one emoji. No hashtags, no "
                    "quotes, no sales pitch.\n"
                    "- Never invent details about a specific house.\n"
                    "Output ONLY the reply text."
                )
            )

            reply_message = _reply_comment(
                comment_id,
                ai_comment,
            )

            SocialComment.objects.create(
                post=reel_id,
                comment_id=comment_id,
                # Their words, kept. This is the FAQ pipeline's eventual source
                # of questions — see social/content/faq.py. It was being
                # fetched and thrown away on every run.
                question=(comment or "")[:500],
                # The text we sent, not the API's response object — the old code
                # stored the raw dict in a 200-char field.
                comment=(ai_comment or "")[:200],
                replied=True if reply_message else False,
                self_comment=False,
            )


def reply_comments_instagram():
    _reply_comments_instagram_reels()
    # Called, not merely referenced. The bare name was a no-op that looked like
    # a call, so whenever the post branch gets written it would never have run.
    _reply_comments_instagram_post()
