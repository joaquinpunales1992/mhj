import requests
import json
import re
import random
import shutil
import os
import tempfile
import urllib.parse
from social.constants import *
from ai.hugging import HuggingFaceAI
from ai.cerebras import CerebrasAI
from social.models import SocialPost, SocialComment
from inventory.models import Property, PropertyImage
import time
from django.conf import settings
import os
import shutil
import requests
import tempfile
from moviepy.video.VideoClip import ImageClip
from moviepy import (
    ImageClip,
    concatenate_videoclips,
    TextClip,
    CompositeVideoClip,
    VideoFileClip,
)
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from membership.utils import notify_social_token_expired
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def refresh_access_token():
    def save_token(token):
        with open("social_access_token.json", "w") as f:
            json.dump({"access_token": token}, f)

    url = "https://graph.facebook.com/v19.0/me/accounts/"
    params = {"access_token": PAGE_ACCESS_TOKEN}

    response = requests.get(url, params=params)
    if response.status_code == 200:
        new_token = response.json().get("data")[0].get("access_token")
        save_token(new_token)
        logger.info("Access token refreshed successfully.")
    else:
        logger.error(f"Failed to refresh access token: {response.json()}")
        return None


def get_fresh_token():
    try:
        with open("social_access_token.json", "r") as f:
            return json.load(f)["access_token"]
    except (FileNotFoundError, KeyError):
        return None


def prepare_image_url_for_facebook(image_url):
    image_url = image_url.lstrip("/")
    # Decode the URL twice
    decoded_once = urllib.parse.unquote(image_url)
    decoded_final = urllib.parse.unquote(decoded_once)

    # Ensure the URL starts with 'https://'
    if decoded_final.startswith("media/https:/"):
        image_url = decoded_final.replace("media/https:/", "https://", 1)
    elif decoded_final.startswith("https:/"):
        image_url = decoded_final.replace("https:/", "https://", 1)

    return image_url


def _download_image_to_tempfile(url):
    """Download remote image to a temp file with .jpg extension."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, stream=True, headers=headers)
    response.raise_for_status()

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    with open(tmp_file.name, "wb") as f:
        for chunk in response.iter_content(1024):
            f.write(chunk)
    return tmp_file.name


def _get_random_mp3_full_path(exclude: str) -> str:
    folder_path = os.path.join(settings.STATIC_ROOT, "audios_for_social_posts")

    mp3_files = [
        f for f in os.listdir(folder_path) if f.endswith(".mp3") and f != exclude
    ]
    if not mp3_files:
        return None

    return os.path.join(folder_path, random.choice(mp3_files))


def _sanity_check_ai_caption(ai_caption: str) -> str:
    ai_caption = ai_caption.replace('"', "")

    reversed_text = ai_caption[::-1]
    match = re.search(r"[.!?]", reversed_text)
    if match:
        cut_index = len(ai_caption) - match.start()
        return ai_caption[:cut_index]
    return ai_caption.strip()


def generate_caption_for_post(
    property_location: str,
    property_url: str,
    property_price: float,
    property_building_area: str,
    property_land_area: str,
    last_caption_generated: str,
    use_ai_caption: bool,
):
    caption = f"Location: {property_location} - Price: {property_price} "

    hashtags = "#akiya #japan #japanlife #cheaphouse #vacationhouse #affordablehouse #japanesehouse #myakiyainjapan"

    ai_caption = ""
    if use_ai_caption:
        try:
            cerebras_ai_client = CerebrasAI()
            ai_caption = cerebras_ai_client.generate_text(
                prompt=(
                    f"Generate a catchy Instagram caption for a property in {property_location} priced at {property_price}. "
                    "The caption should be engaging, highlight the unique features of the property, and encourage users to visit the website for more details.\n\n"
                    f"Note that the last caption generated was: {last_caption_generated}\n"
                    "So do not repeat it the same way.\n"
                    "Output ONLY the caption. No bullet points, no quotes, no examples.\n\n"
                )
            )

            ai_caption = _sanity_check_ai_caption(ai_caption)

            caption = (
                ai_caption
                + f"\n\n💰 Price: {property_price}\n📍 Location: {property_location}\n🏡 Building Area:  {property_building_area}\n🌳 Land Area: {property_land_area}\n\n🔗 www.akiyainjapan.com{property_url}\n\n{hashtags}"
            )
            logger.info(f"Caption generated via AI: {caption}")
        except Exception as e:
            caption = f"💰 Price: {property_price}\n📍 Location: {property_location}\n🏡 Building Area:  {property_building_area}\n🌳 Land Area: {property_land_area}\n\n🔗 www.akiyainjapan.com{property_url}\n\n{hashtags}"
            logger.error(f"AI caption generation failed: {e}")
        return ai_caption, caption
    else:
        logger.info("AI caption generation is disabled, using default caption format.")
        return (
            ai_caption,
            f"💰 Price: {property_price}\n📍 Location: {property_location}\n🏡 Building Area:  {property_building_area}\n🌳 Land Area: {property_land_area}\n\n🔗 www.akiyainjapan.com{property_url}\n\n{hashtags}",
        )


def post_to_instagram(
    property: Property, last_caption_generated: str, use_ai_caption: bool
):
    property_image_urls = [image.file.url for image in property.images.all()][:5]

    ai_caption, caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    media_ids = []

    # Upload each image as a carousel item
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            logger.error(f"Error preparing image URL: {e}")
            continue

        upload_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "image_url": image_url,
            "is_carousel_item": True,
            "access_token": get_fresh_token(),
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()

        if "id" in result:
            logger.info(f"Uploaded image for carousel: {image_url}")
            media_ids.append(result["id"])
        else:
            logger.error(f"Failed to upload image: {result}")

    # Create carousel container with the uploaded media IDs
    if media_ids:
        carousel_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(media_ids),
            "caption": caption,
            "access_token": get_fresh_token(),
        }

        carousel_response = requests.post(carousel_url, data=payload)
        result = carousel_response.json()

        if "id" in result:
            creation_id = result["id"]
            publish_url = (
                f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            )
            publish_payload = {
                "creation_id": creation_id,
                "access_token": get_fresh_token(),
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            if publish_response.status_code == 200:
                logger.info("Successfully posted carousel to Instagram.")
                SocialPost.objects.create(
                    ai_caption=ai_caption,
                    caption=caption,
                    property_url=property.url,
                    social_media="instagram",
                )
            else:
                logger.error(f"Failed to publish carousel: {publish_response.json()}")
        else:
            logger.error(
                f"Failed to create carousel container: {carousel_response.json()}"
            )
    else:
        logger.warning("No images were uploaded; skipping Instagram post.")


def post_to_facebook(
    property: Property, last_caption_generated: str, use_ai_caption: bool
):
    property_image_urls = [image.file.url for image in property.images.all()][:5]

    ai_caption, caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    # Upload each image (unpublished)
    media_fbids = []
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            logger.error(f"Error preparing image URL: {e}")
            continue

        upload_url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/photos"
        payload = {
            "url": image_url,
            "published": "false",
            "access_token": get_fresh_token(),
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()

        if response.status_code == 200 and "id" in result:
            logger.info(f"Uploaded image: {image_url}")
            media_fbids.append(result["id"])
        else:
            logger.error(f"Failed to upload image: {result}")

    # Create the post with all attached media
    if media_fbids:
        post_url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
        payload = {
            "message": caption,
            "access_token": get_fresh_token(),
        }
        for i, media_id in enumerate(media_fbids):
            payload[f"attached_media[{i}]"] = f'{{"media_fbid":"{media_id}"}}'

        response = requests.post(post_url, data=payload)
        result = response.json()

        if response.status_code == 200:
            logger.info("Successfully posted to Facebook with multiple images.")
            SocialPost.objects.create(
                ai_caption=ai_caption,
                caption=caption,
                property_url=property.url,
                social_media="facebook",
            )
        else:
            logger.error(f"Failed to create post: {result}")
    else:
        logger.warning("No images were uploaded; skipping Facebook post.")


def post_instagram_reel():
    try:
        instagram_reels = SocialPost.objects.filter(
            social_media="instagram", content_type="reel"
        )
        last_reel_posted_sound_track = (
            instagram_reels.order_by("-datetime").first().sound_track
            if instagram_reels
            else None
        )
        instagram_reels_urls = instagram_reels.values_list("property_url", flat=True)

        last_caption_generated = (
            instagram_reels.order_by("-datetime").first().ai_caption
            if instagram_reels
            else None
        )

        property_to_post_instagram_reel = (
            Property.objects.filter(
                images__isnull=False, price__lte=PRICE_LIMIT_INSTAGRAM, featured=True
            )
            .exclude(url__in=instagram_reels_urls)
            .order_by("price")
            .distinct()
            .first()
        )

        if not property_to_post_instagram_reel:
            logger.warning("No suitable property found to post on Instagram Reels.")
            return

        audio_path = _get_random_mp3_full_path(exclude=last_reel_posted_sound_track)
        create_property_video(
            property_to_post_instagram_reel.pk,
            output_path="property_video.mp4",
            audio_path=audio_path,
            duration_per_image=3,
        )

        media_dir = os.path.join(settings.MEDIA_ROOT, "generated_videos")
        os.makedirs(media_dir, exist_ok=True)
        target_path = os.path.join(media_dir, "property_video.mp4")
        shutil.move("property_video.mp4", target_path)

        video_url = "https://akiyainjapan.com/media/generated_videos/property_video.mp4"

        ai_caption, caption = generate_caption_for_post(
            property_to_post_instagram_reel.location,
            property_to_post_instagram_reel.get_public_url,
            property_to_post_instagram_reel.get_price_for_front,
            property_to_post_instagram_reel.building_area,
            property_to_post_instagram_reel.land_area,
            last_caption_generated=last_caption_generated,
            use_ai_caption=USE_AI_CAPTION,
        )

        # Step 1: Create media container
        media_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        media_payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": False,
            "access_token": get_fresh_token(),
        }
        media_response = requests.post(media_url, data=media_payload)
        logger.info("Media upload response: " + media_response.text)

        time.sleep(180)
        if "id" in media_response.json():
            creation_id = media_response.json()["id"]

            # Step 2: Publish the video
            publish_url = (
                f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            )
            publish_payload = {
                "creation_id": creation_id,
                "access_token": get_fresh_token(),
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            logger.info("Publish response: " + publish_response.text)

            if publish_response.status_code == 200:
                media_id = publish_response.json().get("id")
                logger.info("✅ Successfully posted to Instagram Reels!")

                # Step 3: Add comment to the published Reel
                if media_id:
                    comment_payload = {
                        "message": DEFAULT_COMMENT,
                        "access_token": get_fresh_token(),
                    }
                    comment_url = (
                        f"https://graph.facebook.com/v19.0/{media_id}/comments"
                    )
                    comment_response = requests.post(comment_url, data=comment_payload)
                    logger.info("Comment response: " + comment_response.text)

                # Log the post
                SocialPost.objects.create(
                    ai_caption=ai_caption,
                    caption=caption,
                    property_url=property_to_post_instagram_reel.url,
                    social_media="instagram",
                    content_type="reel",
                    sound_track=audio_path,
                )
            else:
                logger.error("Failed to publish Reel: " + publish_response.text)
        else:
            logger.error("Failed to create media container: " + media_response.text)
    except Exception as e:
        logger.error(f"Error posting Instagram Reel: {e}")
        notify_social_token_expired(message=f"Error posting Instagram Reel: {e}")


def post_facebook_reel():
    try:
        # Get previously posted properties
        facebook_reels = SocialPost.objects.filter(
            social_media="facebook", content_type="reel"
        )

        last_reel_posted_sound_track = ""
        if facebook_reels:
            last_reel_posted_sound_track = (
                facebook_reels.order_by("-datetime").first().sound_track
                if facebook_reels
                else None
            )
        facebook_reels_urls = facebook_reels.values_list("property_url", flat=True)
        last_caption_generated = (
            facebook_reels.order_by("-datetime").first().ai_caption
            if facebook_reels
            else None
        )

        # Pick a new property to post
        property_to_post_facebook_reel = (
            Property.objects.filter(
                images__isnull=False, price__lte=PRICE_LIMIT_INSTAGRAM, featured=True
            )
            .exclude(url__in=facebook_reels_urls)
            .order_by("price")
            .distinct()
            .first()
        )

        if not property_to_post_facebook_reel:
            logger.warning("No suitable property found to post on Facebook Reels.")
            return

        # Create video
        audio_path = _get_random_mp3_full_path(exclude=last_reel_posted_sound_track)
        create_property_video(
            property_to_post_facebook_reel.pk,
            output_path="property_video.mp4",
            audio_path=audio_path,
            duration_per_image=3,
        )

        media_dir = os.path.join(settings.MEDIA_ROOT, "generated_videos")
        os.makedirs(media_dir, exist_ok=True)
        target_path = os.path.join(media_dir, "property_video.mp4")
        shutil.move("property_video.mp4", target_path)

        video_url = "https://akiyainjapan.com/media/generated_videos/property_video.mp4"

        ai_caption, caption = generate_caption_for_post(
            property_to_post_facebook_reel.location,
            property_to_post_facebook_reel.get_public_url,
            property_to_post_facebook_reel.get_price_for_front,
            property_to_post_facebook_reel.building_area,
            property_to_post_facebook_reel.land_area,
            last_caption_generated=last_caption_generated,
            use_ai_caption=USE_AI_CAPTION,
        )

        # Step: Upload video to Facebook Page
        page_id = PAGE_ID
        access_token = get_fresh_token()

        upload_url = f"https://graph.facebook.com/v19.0/{page_id}/videos"
        payload = {
            "file_url": video_url,
            "description": caption,
            "published": "true",
            "access_token": access_token,
        }

        response = requests.post(upload_url, data=payload)
        logger.info("Facebook video upload response: " + response.text)

        if response.status_code == 200 and "id" in response.json():
            logger.info("Successfully posted to Facebook Reels!")

            # Log the post
            SocialPost.objects.create(
                ai_caption=ai_caption,
                caption=caption,
                property_url=property_to_post_facebook_reel.url,
                social_media="facebook",
                content_type="reel",
                sound_track=audio_path,
            )
        else:
            logger.error("Failed to post Facebook Reel: " + response.text)

    except Exception as e:
        logger.error(f"Error posting Facebook Reel: {e}")
        notify_social_token_expired(message=f"Error posting Facebook Reel: {e}")


def create_property_video(
    property_id: int, output_path: str, audio_path: str, duration_per_image: int = 3
):
    images = PropertyImage.objects.filter(property_id=property_id).order_by("id")[:4]
    property = Property.objects.get(pk=property_id)
    if not images:
        logger.error("No images found for the property.")
        return

    clips = []

    for img_obj in images:
        # Use the correct field name for your image URL here:
        img_url = prepare_image_url_for_facebook(
            img_obj.file.url
        )  # ← change if your field is different
        logger.info(f"Preparing image URL: {img_url}")
        try:
            local_path = _download_image_to_tempfile(img_url)
            clip = ImageClip(local_path, duration=duration_per_image)

            if clip.w % 2 != 0 or clip.h % 2 != 0:
                clip = clip.resized((clip.w + (clip.w % 2), clip.h + (clip.h % 2)))

            clips.append(clip)
        except Exception as e:
            logger.warning(f" Skipping image {img_url}: {e}")

    if not clips:
        logger.error("No valid images to create video.")
        return

    final_video = concatenate_videoclips(clips, method="compose")

    # Write final video
    final_video.write_videofile(
        "property_video_without_label.mp4",
        fps=30,
        codec="libx264",
        audio=audio_path,
        bitrate="3500k",
        preset="medium",
        ffmpeg_params=[
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-g",
            "60",
            "-sc_threshold",
            "0",
        ],
    )
    clip = VideoFileClip("property_video_without_label.mp4").subclipped(
        0, images.count() * duration_per_image
    )

    video_text = (
        f"{property.get_price_for_front}\n{property.get_location_for_front()} \n "
    )

    text_clip = (
        TextClip(
            font=os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Bold.ttf"),
            text=video_text,
            font_size=30,
            color="white",
        )
        .with_duration(images.count() * duration_per_image)
        .with_position((0.1, 0.7), relative=True)
    )

    text_clip_top = (
        TextClip(
            font=os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Light.ttf"),
            text="Link in Bio \n ",
            font_size=30,
            color="white",
        )
        .with_duration(images.count() * duration_per_image)
        .with_position(("center", 0.03), relative=True)
    )

    final_video = CompositeVideoClip([clip, text_clip, text_clip_top])
    final_video.write_videofile(output_path)
    logger.info(f"Video created: {output_path}")
