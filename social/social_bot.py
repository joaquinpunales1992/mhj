from social.constants import *
from ai.hugging import HuggingFaceAI
from social.models import SocialPost
from inventory.models import Property
from social.utils import post_to_facebook, post_to_instagram, post_instagram_reel
import requests


def post_instagram_reel():
    post_instagram_reel()


def post_on_facebook_batch(price_limit: int, batch_size: int):
    facebook_posted_urls = SocialPost.objects.filter(
        social_media="facebook"
    ).values_list("property_url", flat=True)
    properties_to_post_facebook = (
        Property.objects.filter(
            images__isnull=False, price__lte=price_limit, featured=True
        )
        .exclude(url__in=facebook_posted_urls)
        .order_by("price")
        .distinct()[:batch_size]
    )
    for property in properties_to_post_facebook:
        try:
            post_to_facebook(property=property, use_ai_caption=USE_AI_CAPTION)
        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue


def post_on_instagram_batch(price_limit: int, batch_size: int):
    instagram_posted_urls = SocialPost.objects.filter(
        social_media="instagram"
    ).values_list("property_url", flat=True)

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
            post_to_instagram(property=property, use_ai_caption=USE_AI_CAPTION)

        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue


# TODO
# GROUP_ID = ''

# def post_on_facebook_group():
#     post_url = f"https://graph.facebook.com/v19.0/{GROUP_ID}/feed"
#     payload = {
#         "message": "Check out this new property in Kyoto!",
#         "access_token": get_fresh_token()
#     }

#     response = requests.post(post_url, data=payload)
#     if response.status_code == 200:
#         print("✅ Successfully posted to Facebook Group.")
#     else:
#         print(f"❌ Failed to post to Facebook Group: {response.json()}")
