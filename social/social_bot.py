from social.constants import *
from ai.hugging import HuggingFaceAI
from social.models import SocialPost
from inventory.models import Property
from social.utils import post_to_facebook, post_to_instagram, post_instagram_reel
import requests


def post_instagram_reel():
    post_instagram_reel()


def post_on_facebook_batch(price_limit: int, batch_size: int):
    facebook_posts = SocialPost.objects.filter(
        social_media="facebook"
    )
    
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
            last_caption_generated = facebook_posts.order_by("-datetime").first().caption if facebook_posts else None
            post_to_facebook(property=property, last_caption_generated=last_caption_generated, use_ai_caption=USE_AI_CAPTION)
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
            last_caption_generated = instagram_posts.order_by("-datetime").first().caption if instagram_posts else None

            post_to_instagram(property=property, last_caption_generated=last_caption_generated, use_ai_caption=USE_AI_CAPTION)

        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue