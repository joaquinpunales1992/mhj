from django.urls import reverse
from inventory.models import Property
from xml.etree.ElementTree import Element, SubElement, tostring
from django.http import HttpResponse

def display_sitemaps(request):
    views = [
        {'url': reverse('home'), 'priority': '1.0', 'changefreq': 'weekly'},
    ]

    # Property pages
    for property in Property.objects.filter(show_in_front=True, premium=False):
        views.append({'url': f"contact-seller/{property.pk}/", 'priority': '0.6', 'changefreq': 'monthly'},)

    # Create XML structure
    urlset = Element('urlset', xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    for view in views:
        url = SubElement(urlset, 'url')
        loc = SubElement(url, 'loc')
        loc.text = request.build_absolute_uri(view['url'])
        changefreq = SubElement(url, 'changefreq')
        changefreq.text = view['changefreq']
        priority = SubElement(url, 'priority')
        priority.text = view['priority']

    sitemap_xml = tostring(urlset, encoding='utf-8', method='xml')

    return HttpResponse(sitemap_xml, content_type='application/xml')