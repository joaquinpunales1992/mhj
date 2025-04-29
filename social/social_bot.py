import requests
import urllib.parse
from llama_cpp import Llama


PAGE_ACCESS_TOKEN = ''
PAGE_ID = '' 

DOMAIN_CONTEXT = (
    "Generate a text for a website that sells houses in Japan. "
    "Create an engaging text for a property listing using the property location and price. "
    "Limit the caption to 100 characters."
)

def load_llm_model(load_local_model=True):
    if load_local_model:
        return Llama(model_path="social/qwen2-0_5b-instruct-q4_k_m.gguf") 
    else:
        return Llama.from_pretrained(
                repo_id="Qwen/Qwen2-0.5B-Instruct-GGUF",
            filename="qwen2-0_5b-instruct-q4_0.gguf",
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

def post_to_facebook(property_image_url, property_location, property_price, use_ai_caption=True):
    
    regular_caption = f"Location: {property_location} - Price: {property_price}"
    if use_ai_caption:
        try:
            llm_model = load_llm_model()
            # Generate caption using LLM
            caption_prompt = f"Write an engaing caption for a Facebook post: {regular_caption}"
            caption_full_prompt = f"{DOMAIN_CONTEXT}\nUser: {caption_prompt}\nBot:"
            output = llm_model(caption_full_prompt, max_tokens=50)
            caption = output["choices"][0]["text"].strip()
            print (f"*********Generated caption: {caption}")

        except Exception as e:
            print(f"Error generating caption: {e}")
            caption = regular_caption
    else:
        caption = regular_caption
    


    # Post to Facebook
    url = f'https://graph.facebook.com/v19.0/{PAGE_ID}/photos'
    
    image_url = prepare_image_url_for_facebook(property_image_url)
    
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



post_to_facebook(
    property_image_url='https://image.homes.co.jp/smallimg/2023/09/12/20230912_1_1_1_1_1_1_1_1.jpg',
    property_location='Tokyo, Japan',
    property_price='¥100,000,000',
    use_ai_caption=True
)