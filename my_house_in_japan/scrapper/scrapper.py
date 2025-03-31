
# Detached houses in Tokyo for Sale (second-hand)
# https://www.homes.co.jp/kodate/chuko/tokyo/list/

import requests
from bs4 import BeautifulSoup
import os
import sys
import chardet
from deep_translator import GoogleTranslator


def get_listing_urls():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/5374"
    }
    
    keep_looking = True
    listings_url_list = []
    page_number = 1

    while keep_looking:
        url = f"https://www.homes.co.jp/kodate/chuko/tokyo/list/?page={page_number}"

        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            print(f"Failed to retrieve data: {response.status_code}")
            keep_looking = False
            break
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the listings
        listings = soup.find_all('div', class_='mod-mergeBuilding--sale cKodate ui-frame ui-frame-cacao-bar')
        
        # extract the url of the listing
        listings_url_list = [listing.find('div', class_='moduleInner').find('div', class_='moduleBody').find('a').get('href') for listing in listings]
        print (listings_url_list)
        page_number += 1

            
    return listings_url_list
        
        
def get_listing_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/5374"
    }
    
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
        'property_title': google_translator.translate(property_title),
        'property_price': google_translator.translate(table_data['価格']),
        'floor_plan': google_translator.translate(table_data['間取り']),
        'building_area': google_translator.translate(table_data['建物面積']),
        'land_area': google_translator.translate(table_data['土地面積']),
        'parking': google_translator.translate(table_data['駐車場']),
        'building_age': google_translator.translate(table_data['築年月']),
        'location': google_translator.translate(table_data['所在地']),
        'traffic': google_translator.translate(table_data['交通']),
        'building_structure': google_translator.translate(table_data['建物構造']),
        'road_condition': google_translator.translate(table_data['接道状況']),
        'setback': google_translator.translate(table_data['セットバック']),
        'city_planning': google_translator.translate(table_data['都市計画']),
        'zoning': google_translator.translate(table_data['用途地域']),
        'land_category': google_translator.translate(table_data['地目']),
        'building_coverage_ratio': google_translator.translate(table_data['建ぺい率']),
        'floor_area_ratio': google_translator.translate(table_data['容積率']),
        'current_status': google_translator.translate(table_data['現況']),
        'handover': google_translator.translate(table_data['引渡し']),
        # 'equipment': google_translator.translate(table_data['設備']), TODO:FIX
        'transaction_type': google_translator.translate(table_data['取引態様']),
        'remarks': google_translator.translate(table_data['備考']),
        'image_urls': image_urls
    }

    return listing_data




def get_properties():
    # listing_urls = get_listing_urls()
    listing_urls = [
        'https://www.homes.co.jp/kodate/b-1118600001512/',
        # 'https://www.homes.co.jp/kodate/b-1474730000032/',
        # 'https://www.homes.co.jp/kodate/b-1120410015607/',
        # 'https://www.homes.co.jp/kodate/b-1184720013058/'
    ]
    properties_data = []
    for url in listing_urls:
        properties_data.append(get_listing_data(url=url))
    return properties_data


