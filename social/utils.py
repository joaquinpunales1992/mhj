import requests
import json
import urllib.parse
from social.constants import *
from ai.hugging import HuggingFaceAI
from social.models import SocialPost
from inventory.models import Property


def refresh_access_token():
    def save_token(token):
        with open("social_access_token.json", "w") as f:
            json.dump({"access_token": token}, f)

    url = "https://graph.facebook.com/v19.0/me/accounts/"
    params = {
        "access_token": PAGE_ACCESS_TOKEN
    }

    response = requests.get(url, params=params)
    if response.status_code == 200:
        new_token = response.json().get('data')[0].get('access_token')
        save_token(new_token)
        print("✅ Access token refreshed successfully.")
    else:
        print(f"❌ Failed to refresh access token: {response.json()}")
        return None
    
def get_fresh_token():
    try:
        with open("social_access_token.json", "r") as f:
            return json.load(f)["access_token"]
    except (FileNotFoundError, KeyError):
        return None


def prepare_image_url_for_facebook(image_url):
    image_url = image_url.lstrip('/')
    # Decode the URL twice
    decoded_once = urllib.parse.unquote(image_url)
    decoded_final = urllib.parse.unquote(decoded_once)
    
    # Ensure the URL starts with 'https://'
    if not decoded_final.startswith('https://'):
        image_url = decoded_final.replace('https:/', 'https://', 1)
    
    return image_url

def generate_caption_for_post(property_location: str, property_url: str, property_price: float, use_ai_caption: bool):
    caption = f"Location: {property_location} - Price: {property_price} "
    
    if use_ai_caption:
        try:
            ai = HuggingFaceAI()
            caption = ai.invoke_ai(
                instruction=f"Generate a catchy Instagram caption for a property in {property_location} priced at {property_price}. The caption should be engaging, highlight the unique features of the property, and encourage users to visit the website for more details."
            )
            caption = caption.replace('"', '')
      
            if not caption.endswith('.'):
                caption = caption[:caption.rfind('.') + 1]
            caption += f"\n\nPrice: {property_price}\nLocation: {property_location}\n\nFind it at {property_url}\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"

        except Exception as e:
            print(f"Error generating caption: {e}")
            caption = f"\n\nPrice: {property_price}\nLocation: {property_location}\n\nFind it at {property_url}\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"

        return caption
    else:
        return f"Price: {property_price}\nLocation: {property_location}\n\nFind it at {property_url}\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"
    

def post_to_instagram(property: Property, use_ai_caption: bool = True):
    property_image_urls=[image.file.url for image in property.images.all()][:5]
    
    caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        use_ai_caption=use_ai_caption
    )

    media_ids = []

    # Upload each image as a carousel item
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            print(f"Error preparing image URL: {e}")
            continue

        upload_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "image_url": image_url,
            "is_carousel_item": True,
            "access_token": get_fresh_token()
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()
        
        if 'id' in result:
            print(f"✅ Uploaded image for carousel: {image_url}")
            media_ids.append(result['id'])
        else:
            print(f"❌ Failed to upload image: {result}")

    # Create carousel container with the uploaded media IDs
    if media_ids:
        carousel_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(media_ids),
            "caption": caption,
            "access_token": get_fresh_token()
        }

        carousel_response = requests.post(carousel_url, data=payload)
        result = carousel_response.json()

        if 'id' in result:
            creation_id = result['id']
            publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            publish_payload = {
                "creation_id": creation_id,
                "access_token": get_fresh_token()
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            if publish_response.status_code == 200:
                print('✅✅✅ Successfully posted carousel to Instagram.')
                SocialPost.objects.create(
                    caption=caption,
                    property_url=property.url,
                    social_media='instagram'
                )
            else:
                print(f'❌ Failed to publish carousel: {publish_response.json()}')
        else:
            print(f'❌ Failed to create carousel container: {result}')
    else:
        print("❌ No media uploaded; skipping Instagram post.")


def post_to_facebook(property: Property, use_ai_caption: bool =True):
    property_image_urls = [image.file.url for image in property.images.all()][:5]

    caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        use_ai_caption=use_ai_caption
    )

    # Upload each image (unpublished)
    media_fbids = []
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            print(f"Error preparing image URL: {e}")
            continue
        
        upload_url = f'https://graph.facebook.com/v19.0/{PAGE_ID}/photos'
        payload = {
            'url': image_url,
            'published': 'false',
            'access_token': get_fresh_token(),
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()
        
        if response.status_code == 200 and 'id' in result:
            print(f'✅ Uploaded image: {image_url}')
            media_fbids.append(result['id'])
        else:
            print(f'❌ Failed to upload image: {result}')

    # Create the post with all attached media
    if media_fbids:
        post_url = f'https://graph.facebook.com/v19.0/{PAGE_ID}/feed'
        payload = {
            'message': caption,
            'access_token': get_fresh_token(),
        }
        for i, media_id in enumerate(media_fbids):
            payload[f'attached_media[{i}]'] = f'{{"media_fbid":"{media_id}"}}'

        response = requests.post(post_url, data=payload)
        result = response.json()

        if response.status_code == 200:
            print('✅✅✅ Successfully posted to Facebook with multiple images.')
            SocialPost.objects.create(
                caption=caption,
                property_url=property.url,
                social_media='facebook'
            )
        else:
            print(f'❌ Failed to create post: {result}')
    else:
        print("❌ No images were uploaded; skipping post.")
