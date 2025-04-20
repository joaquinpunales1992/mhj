import locale
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

def convert_price_string(price):
    try:
        # Convert to int and multiply by 10,000
        return int(price) * 10000
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid price: {price}")
    
def convert_yen_to_usd(price):
    return locale.currency(float(price) * 0.007, grouping=True).replace('.00', '')

def infer_location(location):
    if "tokyo" in location.lower():
        return "Tokyo"
    elif "osaka" in location.lower():
        return "Osaka"
    elif 'shizuoka' in location.lower():
        return "Shizuoka"
    elif 'kanagawa' in location.lower():
        return "Kanagawa"
    elif 'aichi' in location.lower():
        return "Aichi"
    elif 'hyogo' in location.lower():
        return "Hyogo"
    elif 'chiba' in location.lower():
        return "Chiba"
    elif 'saitama' in location.lower():
        return "Saitama"
    elif 'fukuoka' in location.lower():
        return "Fukuoka"
    elif 'hiroshima' in location.lower():
        return "Hiroshima"
    elif 'kyoto' in location.lower():
        return "Kyoto"
    elif 'nagoya' in location.lower():
        return "Nagoya"
    elif 'kagawa' in location.lower():
        return "Kagawa"
    elif 'okayama' in location.lower():
        return "Okayama"
    elif 'miyagi' in location.lower():
        return "Miyagi"
    elif 'niigata' in location.lower():
        return "Niigata"
    elif 'ishikawa' in location.lower():
        return "Ishikawa"
    elif 'nagano' in location.lower():
        return "Nagano"
    elif 'gunma' in location.lower():
        return "Gunma"
    elif 'tochigi' in location.lower():
        return "Tochigi"
    elif 'ibaraki' in location.lower():
        return "Ibaraki"
    elif 'yamagata' in location.lower():
        return "Yamagata"
    elif 'fukushima' in location.lower():
        return "Fukushima"
    elif 'shimane' in location.lower():
        return "Shimane"
    elif 'tottori' in location.lower():
        return "Tottori"
    elif "nagasaki" in location.lower():
        return "Nagasaki"
    elif "kumamoto" in location.lower():
        return "Kumamoto"
    elif "ehime" in location.lower():
        return "Ehime"
    elif "kagoshima" in location.lower():
        return "Kagoshima"
    elif "okinawa" in location.lower():
        return "Okinawa"
    elif "aomori" in location.lower():
        return "Aomori"
    elif "akita" in location.lower():
        return "Akita"
    elif "yamaguchi" in location.lower():
        return "Yamaguchi"
    elif "toyama" in location.lower():
        return "Toyama"
    elif "gifu" in location.lower():
        return "Gifu"
    elif "shizuoka" in location.lower():
        return "Shizuoka"
    elif "wakayama" in location.lower():
        return "Wakayama"
    elif "nara" in location.lower():
        return "Nara"
    elif "miyazaki" in location.lower():    
        return "Miyazaki"
    elif "kagawa" in location.lower():  
        return "Kagawa"
    elif "yamaguchi" in location.lower():
        return "Yamaguchi"
    else:
        return location
