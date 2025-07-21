from social.constants import *
import logging
from ai.hugging import HuggingFaceAI
from ai.cerebras import CerebrasAI
from social.models import SocialPost, SocialComment
from inventory.models import Property
from social.utils import (
    post_to_facebook,
    post_to_instagram,
    post_instagram_reel,
    get_fresh_token,
)
from social.constants import INSTAGRAM_USER_ID
import requests

logger = logging.getLogger(__name__)

def post_instagram_reel():
    post_instagram_reel()


def post_on_facebook_batch(price_limit: int, batch_size: int):
    facebook_posts = SocialPost.objects.filter(social_media="facebook")

    facebook_posts_urls = facebook_posts.values_list("property_url", flat=True)

    properties_to_post_facebook = (
        Property.objects.filter(
            images__isnull=False, price__lte=price_limit, featured=True
        )
        .exclude(url__in=facebook_posts_urls)
        .order_by("price")
        .distinct()[:batch_size]
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

    instagram_posted_urls = instagram_posts.values_list("property_url", flat=True)

    properties_to_post_instagram = (
        Property.objects.filter(
            images__isnull=False, price__lte=price_limit, featured=True
        )
        .exclude(url__in=instagram_posted_urls)
        .order_by("price")
        .distinct()[:batch_size]
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
        logger.error("Error replying:", response.text)
        return None
    
def _get_reels():
    import pdb;pdb.set_trace()
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
        replied_social_comments_ids_per_reel = SocialComment.objects.filter(Q(
            post=reel_id, replied=True) | Q(self_comment=True)
        ).values_list("comment_id", flat=True)

        for comment in comments:
            comment_id = comment["id"]
            comment = comment["text"]

            if int(comment_id) in replied_social_comments_ids_per_reel or comment == DEFAULT_COMMENT:
                continue

            cerebras_ai_client = CerebrasAI()
            ai_comment = cerebras_ai_client.generate_text(
                prompt=(
                    f"Generate a friendly response for a social media comment. Encourage the person to check the link in our bio.\n\n"
                    "Output ONLY the caption. No bullet points, no quotes, no examples.\n\n"
                )
            )

            reply_message = _reply_comment(
                comment_id,
                ai_comment,
            )

            SocialComment.objects.create(
                post=reel_id,
                comment_id=comment_id,
                comment=reply_message,
                replied=True if reply_message else False,
                self_comment=False
            )


def reply_comments_instagram():
    _reply_comments_instagram_reels()
    _reply_comments_instagram_post

