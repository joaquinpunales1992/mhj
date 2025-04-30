import requests
import urllib.parse
from llama_cpp import Llama


PAGE_ACCESS_TOKEN = 'EAAKF3IrNfYMBO4hvRjsO0ZBB74AVZAwkM4NbBu2YuANElCxDyH6VpZB8r9KDcGOD9l1TP9WQIRY4rmf2oW6rZBZBmgBfxlBSqNCiMPqW4uijBKcZAflFekZCKz68K0boAAIrZBFR5oTs7LtSEepv8Xo7pEqbSqBNSwdMyZA04x1oBdZC1NzwtzNuZAmJcT0pxtfX6WhhVRvvBRybd4ZD'
PAGE_ID = '612249001976104' 
INSTAGRAM_USER_ID = '17841473089014615'

DOMAIN_CONTEXT = (
    "Generate a Facebook post caption for a website that sells houses in Japan for foreigners. "
    "Use only the information provided, such as property location and price — do not assume any extra details. "
    "Write an engaging caption to promote the property. "
    "Keep the total caption under 100 characters. "
    "Ensure the main descriptive part is no more than 65 characters. "
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

        except Exception as e:
            print(f"Error generating caption: {e}")
            caption = regular_caption
    else:
        caption = regular_caption

    # Add additional text to the caption
    caption += f"\n\nPrice: {property_price}\nLocation: {property_location}\n\nFind out more at https://akiyainjapan.com\n\n #akiya #japan #japanlife #cheaphouses #myakiyainjapan"
    return caption


def post_to_instagram(property_image_url, property_location, property_price, use_ai_caption):

    caption = generate_caption_for_post(property_location=property_location, property_price=property_price, use_ai_caption=use_ai_caption)

    load_media_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"

    image_url = prepare_image_url_for_facebook(property_image_url)
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": PAGE_ACCESS_TOKEN
    }

    response = requests.post(load_media_url, data=payload)
    if 'id' in response.json():
        creation_id = response.json().get('id')
        publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
        publish_payload = {
            "creation_id": creation_id,
            "access_token": PAGE_ACCESS_TOKEN
        }   
        publish_response = requests.post(publish_url, data=publish_payload)
        if publish_response.status_code == 200:
            print('✅ Successfully posted to Instagram:')
            print(publish_response.json())
        else:
            print('❌ Failed to publish Instagram post:')
            print(publish_response.text)


def post_to_facebook(property_image_url, property_location, property_price, use_ai_caption=True):
    caption = generate_caption_for_post(property_location=property_location, property_price=property_price, use_ai_caption=use_ai_caption)
    
    # Post to Facebook
    url = f'https://graph.facebook.com/v19.0/{PAGE_ID}/photos'
    
    try:
        image_url = prepare_image_url_for_facebook(property_image_url)
    except Exception as e:
        print(f"Error preparing image URL: {e}")
        return
    
    payload = {
        'url': image_url,
        'caption': caption,
        'access_token': PAGE_ACCESS_TOKEN,
    }
    response = requests.post(url, data=payload)

    if response.status_code == 200:
        print('✅ Successfully posted to Facebook:')
        print(response.json())
    else:
        print('❌ Failed to post:')
        print(response.text)


# RUN SOCIAL BOT
from inventory.models import Property
properties_qs = Property.objects.filter(images__isnull=False, price__lte=1200).distinct()#[1:1000]

load_llm_model()

image_url = Property.objects.filter(images__isnull=False).first().images.first().file.url


# INSTAGRAM POSTING
for property in properties_qs:
    try:
        post_to_instagram(
            property_image_url=property.images.first().file.url,
            property_location=property.location,
            property_price=property.get_price_for_front,
            use_ai_caption=True
        )
    except Exception as e:
        print(f"Error posting property {property.id}: {e}")
        continue

# FACEBOOK POSTING
for property in properties_qs:
    try:
        post_to_facebook(
            property_image_url=property.images.first().file.url,
            property_location=property.location,
            property_price=property.get_price_for_front,
            use_ai_caption=True
        )
    except Exception as e:
        print(f"Error posting property {property.id}: {e}")
        continue