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
    return locale.currency(price * 0.007, grouping=True)

def infer_location(location):
    if "tokyo" in location.lower():
        return "Tokyo"
    elif "osaka" in location.lower():
        return "Osaka"
    else:
        return location
