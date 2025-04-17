
# Detached houses in Tokyo for Sale (second-hand)
# https://www.homes.co.jp/kodate/chuko/tokyo/list/

import requests
from bs4 import BeautifulSoup
import os
import sys
import chardet
from deep_translator import GoogleTranslator
from inventory.models import Property, PropertyImage


MAX_PAGE = 10

def get_listing_urls():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/5374"
    }
    
    keep_looking = True
    listings_url_list = []
    page_number = 1

    while keep_looking:
        # url = f"https://toushi.homes.co.jp/bukkensearch/?page={page_number}"
        url = f"https://www.homes.co.jp/kodate/chuko/tokyo/list/?page={page_number}"

        response = requests.get(url, headers=headers)

        if response.status_code != 200 or page_number == MAX_PAGE:
            print(f"Failed to retrieve data: {response.status_code}")
            keep_looking = False
            break
        
        soup = BeautifulSoup(response.text, 'html.parser')
        # Find the listings
        listings = soup.find_all('div', class_='mod-mergeBuilding--sale cKodate ui-frame ui-frame-cacao-bar')
        
        # extract the url of the listing
        listing_urls = [listing.find('div', class_='moduleInner').find('div', class_='moduleBody').find('a').get('href') for listing in listings]
        for url in listing_urls:
            persist_property(property_data=get_listing_data(url=url))

        page_number += 1
            
    return listings_url_list
        
        
def get_listing_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/5374"
    }

    MAX_CHAR_LENGTH = 5000
    
    response = requests.get(url, headers=headers)

    encoding = chardet.detect(response.content)['encoding']
    response.encoding = encoding

    google_translator = GoogleTranslator(source='auto', target='en')
    if response.status_code != 200:
        print(f"Failed to retrieve data: {response.status_code}")
        return None
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Extract title, classification, type and price
    article_header = soup.find('div', {'data-component': 'ArticleHeader'})
    h1_tag_article = article_header.find('h1')
    property_title = h1_tag_article.find_all('span')[3].get_text(strip=True)
    property_price = soup.find('p', {'data-component': 'price'}).find('span').get_text(strip=True)

    table = soup.find('table', {'class': 'w-full table-fixed'})

    table_data = {
        '価格': None,
        '間取り': None,
        '建物面積': None,
        '土地面積': None,
        '駐車場': None,
        '築年月': None,
        '所在地': None,
        '交通': None,
        '建物構造': None,
        '接道状況': None,
        'セットバック': None,
        '都市計画': None,
        '用途地域': None,
        '地目': None,
        '建ぺい率': None,
        '容積率': None,
        '現況': None,
        '引渡し': None,
        '設備': None,
        '取引態様': None,
        '備考': None
    }
    for row in table.find_all('tr'):
        header = row.find('th').get_text(strip=True)
        if header in table_data:
            table_data[header] = row.find('td').get_text(strip=True)

    # Extract images
    image_urls = []

    ul_element = soup.find('ul', {'data-target': 'photo-slider.slider'})

    if ul_element:
        # Find all <img> tags within the <ul> element
        img_tags = ul_element.find_all('img')
        for img in img_tags:
            img_url = img.get('src')
            if img_url.startswith('https://image.homes.jp/smallimg'):
                image_urls.append(img_url)

    # Add more fields as needed
    listing_data = {
        'property_url': url,
        'property_title': google_translator.translate(property_title),
        'property_price': google_translator.translate(property_price),
        'floor_plan': google_translator.translate(table_data['間取り'][:MAX_CHAR_LENGTH]) if table_data['間取り'] else "",
        'building_area': google_translator.translate(table_data['建物面積'][:MAX_CHAR_LENGTH]) if table_data['建物面積'] else "",
        'land_area': google_translator.translate(table_data['土地面積'][:MAX_CHAR_LENGTH]) if table_data['土地面積'] else "",
        'parking': google_translator.translate(table_data['駐車場'][:MAX_CHAR_LENGTH]) if table_data['駐車場'] else "",
        'building_age': google_translator.translate(table_data['築年月'][:MAX_CHAR_LENGTH]) if table_data['築年月'] else "",
        'location': google_translator.translate(table_data['所在地'][:MAX_CHAR_LENGTH]) if table_data['所在地'] else "",
        'traffic': google_translator.translate(table_data['交通'][:MAX_CHAR_LENGTH]) if table_data['交通'] else "",
        'building_structure': google_translator.translate(table_data['建物構造'][:MAX_CHAR_LENGTH]) if table_data['建物構造'] else "",
        'road_condition': google_translator.translate(table_data['接道状況'][:MAX_CHAR_LENGTH]) if table_data['接道状況'] else "",
        'setback': google_translator.translate(table_data['セットバック'][:MAX_CHAR_LENGTH]) if table_data['セットバック'] else "",
        'city_planning': google_translator.translate(table_data['都市計画'][:MAX_CHAR_LENGTH]) if table_data['都市計画'] else "",
        'zoning': google_translator.translate(table_data['用途地域'][:MAX_CHAR_LENGTH]) if table_data['用途地域'] else "",
        'land_category': google_translator.translate(table_data['地目'][:MAX_CHAR_LENGTH]) if table_data['備考'] else "",
        'building_coverage_ratio': google_translator.translate(table_data['建ぺい率'][:MAX_CHAR_LENGTH]) if table_data['建ぺい率'] else "",
        'floor_area_ratio': google_translator.translate(table_data['容積率'][:MAX_CHAR_LENGTH]) if table_data['容積率'] else "",
        'current_status': google_translator.translate(table_data['現況'][:MAX_CHAR_LENGTH]) if table_data['現況'] else "",
        'handover': google_translator.translate(table_data['引渡し'][:MAX_CHAR_LENGTH]) if table_data['引渡し'] else "",
        # 'equipment': google_translator.translate(table_data['設備']), TODO:FIX
        'transaction_type': google_translator.translate(table_data['取引態様'][:MAX_CHAR_LENGTH]) if table_data['取引態様'] else "",
        'remarks':  google_translator.translate(table_data['備考'][:MAX_CHAR_LENGTH]) if table_data['備考'] else "",
        'image_urls': image_urls
    }

    return listing_data


def persist_property(property_data: dict):
    try:
        property, created = Property.objects.get_or_create(
            url=property_data['property_url']
        )
        if created:
            property.url = property_data['property_url']
            property.title = property_data['property_title']
            property.traffic=property_data['traffic']
            property.location=property_data['location']
            property.description=property_data['remarks']
            property.construction_date=property_data['building_age']
            # property.land_rights=property_data['land_rights']
            property.building_structure=property_data['building_structure']
            property.road_condition=property_data['road_condition']
            property.setback=property_data['setback']
            property.city_planning=property_data['city_planning']
            property.zoning=property_data['zoning']
            property.land_category=property_data['land_category']
            property.building_coverage_ratio=property_data['building_coverage_ratio']
            property.floor_area_ratio=property_data['floor_area_ratio']
            property.current_status=property_data['current_status']
            property.handover=property_data['handover']
            property.transaction_type=property_data['transaction_type']
            property.price=property_data['property_price']
            property.floor_plan=property_data['floor_plan']
            property.building_area=property_data['building_area']
            property.land_area=property_data['land_area']
            property.parking=property_data['parking']
            property.construction=property_data['building_age']
        
            property.save()

            for image_url in property_data.get('image_urls', []):
                PropertyImage.objects.create(property=property, file=image_url)
        
            property.save()
            print(f"Property {property.title} saved successfully.")

    except Exception as e:
        print(f"Error saving property: {e}")
    

get_listing_urls()