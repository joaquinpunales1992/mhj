from django.urls import reverse
from inventory.models import Property
from xml.etree.ElementTree import Element, SubElement, tostring
from django.http import HttpResponse

from front.views import CITY_CATEGORIES


def display_sitemaps(request):
    views = [
        {"url": reverse("home"), "priority": "1.0", "changefreq": "weekly"},
        {"url": reverse("upgrade_premium"), "priority": "0.8", "changefreq": "monthly"},
        {"url": reverse("how_to_buy"), "priority": "0.7", "changefreq": "monthly"},
        {"url": reverse("faqs"), "priority": "0.7", "changefreq": "monthly"},
        {"url": reverse("about"), "priority": "0.6", "changefreq": "monthly"},
    ]

    # Region landing pages (one per prefecture we cover).
    for region in CITY_CATEGORIES:
        views.append(
            {
                "url": reverse("region_listing", args=[region.lower()]),
                "priority": "0.8",
                "changefreq": "weekly",
            }
        )

    # Category landing pages.
    for category in ("beach", "snow", "mountain", "onsen"):
        views.append(
            {
                "url": reverse("filter_properties", args=[category]),
                "priority": "0.7",
                "changefreq": "weekly",
            }
        )

    # Property pages
    for property in Property.objects.filter(show_in_front=True, premium=False):
        views.append(
            {
                "url": f"japanese-houses/{property.pk}/",
                "priority": "0.6",
                "changefreq": "monthly",
            },
        )

    # Create XML structure
    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    for view in views:
        url = SubElement(urlset, "url")
        loc = SubElement(url, "loc")
        loc.text = request.build_absolute_uri(view["url"])
        changefreq = SubElement(url, "changefreq")
        changefreq.text = view["changefreq"]
        priority = SubElement(url, "priority")
        priority.text = view["priority"]

    sitemap_xml = tostring(urlset, encoding="utf-8", method="xml")

    return HttpResponse(sitemap_xml, content_type="application/xml")
