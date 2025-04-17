from my_house_in_japan.scrapper.scrapper import get_properties


def import_property():
    from my_house_in_japan.inventory.models import Property, PropertyImage

    properties_list = get_properties()
    for property_dict in properties_list:
        if 'image_urls' in property_dict:
            for image_url in property_dict.get('image_urls'):
                PropertyImage.objects.create(file=image_url)

        Property.objects.create(
            title=property_dict.get('property_title'),
            price=property_dict.get('property_price'),
            floor_plan=property_dict.get('floor_plan'),
            )

    


import_property()



