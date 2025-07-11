from social.constants import *
from ai.hugging import HuggingFaceAI
from social.models import SocialPost
from inventory.models import Property
from social.utils import post_to_facebook, post_to_instagram
import requests


# TODO:
def post_instagram_reel():
    INSTAGRAM_USER_ID = "17841473089014615"
    ACCESS_TOKEN = "your_facebook_page_access_token"
    VIDEO_URL = "https://akiyainjapan.com/static/assets/video.mp4"  # must be public
    CAPTION = "Experience modern living in Kyoto 🏯✨ #JapanHomes"

    # Step 1: Create media container
    media_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
    media_payload = {
        "media_type": "REELS",
        "video_url": VIDEO_URL,
        "caption": CAPTION,
        "access_token": ACCESS_TOKEN,
    }
    media_response = requests.post(media_url, data=media_payload)
    print("📥 Media upload response:", media_response.text)

    if "id" in media_response.json():
        creation_id = media_response.json()["id"]

        # Step 2: Publish the video
        publish_url = (
            f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
        )
        publish_payload = {"creation_id": creation_id, "access_token": ACCESS_TOKEN}

        publish_response = requests.post(publish_url, data=publish_payload)
        print("🚀 Publish response:", publish_response.text)

        if publish_response.status_code == 200:
            print("✅ Successfully posted to Instagram Reels!")
        else:
            print("❌ Failed to publish Reel.")
    else:
        print("❌ Failed to create media container.")


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
