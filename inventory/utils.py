import locale
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

def convert_price_string(price_str):
    try:
        # Remove whitespace and commas
        numeric_part = price_str.strip().replace(',', '')
        # Convert to int and multiply by 10,000
        return int(numeric_part) * 10000
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid price string: {price_str}")
    
def convert_yen_to_usd(price):
    return locale.currency(price * 0.007, grouping=True).split('.')[0]

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
    else:
        return location
