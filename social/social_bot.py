import requests
import urllib.parse
from llama_cpp import Llama


PAGE_ACCESS_TOKEN = 'EAAKF3IrNfYMBO0dXW4fFV7hd0KJfO9lynVGlbGWiPBUPZCTnKPnUvZBhOIPIZB44yxPZACRvVagyZAFZAdcnPVwq7tMO4vfeMCUf4y4EudWTakblZBbKBVBR38o917pNpfAQQqbCMciaBw7icqb7GK7PZBHeYAJXsMVhJNJ4W2o6s64WZBCZCw1YIGtur5EbcH8mZC3D5KFWNLbhGxUhfmM1mAZD'
PAGE_ID = '612249001976104' 
INSTAGRAM_USER_ID = '17841473089014615'

DOMAIN_CONTEXT = (
    "You are a copywriting assistant for a website that sells houses in Japan to foreigners.\n"
    "Your job is to generate short, engaging Facebook post captions.\n"
    "Use only the information provided (e.g., location and price). Do not make up features.\n"
    "Guidelines:\n"
    "- Total length: max 100 characters\n"
    "- Main descriptive part: max 65 characters\n"
    "- Make it appealing for foreign buyers\n"
    "- Avoid emojis or hashtags\n"
    "- Keep the language natural and friendly\n"
)

def load_llm_model(load_local_model=True):
    global llm_model
    if load_local_model:  
        llm_model = Llama(model_path="social/pretrained_llm_models/qwen2-0_5b-instruct-q4_k_m.gguf")
    else:
        llm_model = Llama.from_pretrained(
            repo_id="Qwen/Qwen2-0.5B-Instruct-GGUF",
            filename="qwen2-0_5b-instruct-fp16.gguf",
            verbose=False,
            max_seq_len=512
        )

def prepare_image_url_for_facebook(image_url):
    image_url = image_url.lstrip('/')
    # Decode the URL twice
    decoded_once = urllib.parse.unquote(image_url)
    decoded_final = urllib.parse.unquote(decoded_once)
    
    # Ensure the URL starts with 'https://'
    if not decoded_final.startswith('https://'):
        image_url = decoded_final.replace('https:/', 'https://', 1)
    
    return image_url

def generate_caption_for_post(property_location, property_price, use_ai_caption=True):
    regular_caption = f"Location: {property_location} - Price: {property_price}"
    
    if use_ai_caption:
        try:
            # Generate caption using LLM
            # caption_prompt = f"Write an engaing caption for a Facebook post: {regular_caption}"
            caption_full_prompt = f"{DOMAIN_CONTEXT}\nUser: {regular_caption}\nBot:"
            output = llm_model(caption_full_prompt, max_tokens=80)

            caption = output["choices"][0]["text"].strip()
            caption = caption.replace('"', '')
            if not caption.endswith('.'):
                caption = caption[:caption.rfind('.') + 1]
            caption += f"\n\nPrice: {property_price}\nLocation: {property_location}\n\nFind out more at https://akiyainjapan.com\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"

        except Exception as e:
            print(f"Error generating caption: {e}")
            caption = f"\n\nPrice: {property_price}\nLocation: {property_location}\n\nFind out more at https://akiyainjapan.com\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"

    return caption



def post_to_instagram(property, use_ai_caption):

    property_url=property.url
    property_image_urls=[image.file.url for image in property.images.all()][:5]
    property_location=property.location
    property_price=property.get_price_for_front
    
    caption = generate_caption_for_post(
        property_location=property_location,
        property_price=property_price,
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
            "access_token": PAGE_ACCESS_TOKEN
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
            "access_token": PAGE_ACCESS_TOKEN
        }

        carousel_response = requests.post(carousel_url, data=payload)
        result = carousel_response.json()

        if 'id' in result:
            creation_id = result['id']
            publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            publish_payload = {
                "creation_id": creation_id,
                "access_token": PAGE_ACCESS_TOKEN
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            if publish_response.status_code == 200:
                print('✅ Successfully posted carousel to Instagram.')
                SocialPost.objects.create(
                    caption=caption,
                    property_url=property_url,
                    social_media='instagram'
                )
            else:
                print(f'❌ Failed to publish carousel: {publish_response.json()}')
        else:
            print(f'❌ Failed to create carousel container: {result}')
    else:
        print("❌ No media uploaded; skipping Instagram post.")



import requests

def post_to_facebook(property, use_ai_caption=True):

    property_url=property.url,
    property_image_urls = [image.file.url for image in property.images.all()][:3]
    property_location=property.location
    property_price=property.get_price_for_front

    caption = generate_caption_for_post(
        property_location=property_location,
        property_price=property_price,
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
            'access_token': PAGE_ACCESS_TOKEN,
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
            'access_token': PAGE_ACCESS_TOKEN,
        }
        for i, media_id in enumerate(media_fbids):
            payload[f'attached_media[{i}]'] = f'{{"media_fbid":"{media_id}"}}'

        response = requests.post(post_url, data=payload)
        result = response.json()

        if response.status_code == 200:
            print('✅ Successfully posted to Facebook with multiple images.')
            SocialPost.objects.create(
                caption=caption,
                property_url=property_url,
                social_media='facebook'
            )
        else:
            print(f'❌ Failed to create post: {result}')
    else:
        print("❌ No images were uploaded; skipping post.")



# RUN SOCIAL BOT
from inventory.models import Property
from social.models import SocialPost

PRICE_LIMIT = 800
INSTGRAM_POST = False
FACEBOOK_POST = True

facebook_posted_urls = SocialPost.objects.filter(
    social_media='facebook'
    ).values_list(
        'property_url',
        flat=True
    )

instagram_posted_urls = SocialPost.objects.filter(social_media='instagram').values_list('property_url', flat=True)

properties_to_post_facebook = Property.objects.filter(images__isnull=False, price__lte=PRICE_LIMIT).exclude(url__in=facebook_posted_urls).order_by('price').distinct() #[1:1000]
properties_to_post_instagram = Property.objects.filter(images__isnull=False, price__lte=PRICE_LIMIT).exclude(url__in=instagram_posted_urls).order_by('price').distinct() #[1:1000]

import pdb;pdb.set_trace()
load_llm_model()

# image_url = Property.objects.filter(images__isnull=False).first().images.first().file.url

if FACEBOOK_POST:
    for property in properties_to_post_facebook:
        try:
            post_to_facebook(property=property, use_ai_caption=True)
        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue

if INSTGRAM_POST:
    for property in properties_to_post_instagram:
        try:
            post_to_instagram(
                property=property,
                use_ai_caption=True)
               
        except Exception as e:
            print(f"Error posting property {property.id}: {e}")
            continue