import urllib.parse
from django.db import models
from django.urls import reverse
from inventory.constants import MIN_AREA_SAMPLE_FOR_COMPARISON
from inventory.utils import (
    convert_price_string,
    convert_yen_to_usd,
    infer_location,
    parse_area_to_m2,
)


class TimestampMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True


class Property(TimestampMixin):
    url = models.URLField(max_length=255, default="", blank=True)
    title = models.CharField(max_length=255, blank=True)
    price = models.IntegerField(default=0)
    building_area = models.CharField(max_length=255, blank=True)
    land_area = models.CharField(max_length=255, blank=True)
    parking = models.CharField(max_length=255, blank=True)
    traffic = models.CharField(max_length=255, default="", blank=True)
    building_structure = models.CharField(max_length=255, default="", blank=True)
    road_condition = models.CharField(max_length=255, default="", blank=True)
    setback = models.CharField(max_length=255, default="", blank=True)
    city_planning = models.CharField(max_length=255, default="", blank=True)
    zoning = models.CharField(max_length=255, default="", blank=True)
    land_category = models.CharField(max_length=255, default="", blank=True)
    building_coverage_ratio = models.CharField(max_length=255, default="", blank=True)
    floor_area_ratio = models.CharField(max_length=255, default="", blank=True)
    current_status = models.CharField(max_length=255, default="", blank=True)
    handover = models.CharField(max_length=255, default="", blank=True)
    transaction_type = models.CharField(max_length=255, default="", blank=True)
    equipment = models.CharField(max_length=255, default="", blank=True)
    floor_plan = models.CharField(max_length=500)
    location = models.CharField(max_length=255, default="")
    construction_date = models.CharField(max_length=255, default="")
    land_rights = models.CharField(max_length=255, default="")
    description = models.TextField(default="", blank=True)  # remarks
    construction = models.CharField(max_length=255, blank=True)
    show_in_front = models.BooleanField(default=True)
    featured = models.BooleanField(default=False)
    premium = models.BooleanField(default=False)
    likes = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Property"
        verbose_name_plural = "Properties"

    def __str__(self):
        return f"{self.title}: {self.price}"

    def get_title_for_front(self):
        return self.title if len(self.title) < 20 else self.title[:20] + "..."

    @property
    def get_price_for_front(self):
        return convert_yen_to_usd(convert_price_string(self.price))

    @property
    def price_yen(self):
        """Raw asking price in yen (price is stored in 万/man units)."""
        return (self.price or 0) * 10000

    def building_area_m2(self):
        """Building floor area as a float (m²), or None. For the unit toggle."""
        return parse_area_to_m2(self.building_area)

    def land_area_m2(self):
        """Land area as a float (m²), or None. For the unit toggle."""
        return parse_area_to_m2(self.land_area)

    def get_location_for_map(self):
        # Scraped locations sometimes have trailing UI junk like
        # "[ ■ Surrounding environment]" (SUUMO) that Google Maps refuses
        # to geocode. Strip everything from the first '[' onward.
        loc = (self.location or "").split("[", 1)[0].strip(" ,")
        return loc

    def get_location_url(self):
        return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(self.get_location_for_map(), safe='')}"

    def property_has_any_image(self):
        return self.images.exists()

    def get_ordered_images(self):
        # -first_image so an image explicitly marked first comes first (True > False).
        # id ascending so when nothing is marked, we get the first photo the scraper
        # inserted — which is the lowest-numbered SUUMO photo, conventionally the
        # main exterior shot. (The old "-id" ordering surfaced the *last* inserted
        # photo, often a marketing flyer at the end of the listing.)
        return self.images.order_by("-first_image", "id")

    def get_location_for_front(self):
        return infer_location(self.location)

    @property
    def get_public_url(self):
        return reverse("property_detail", kwargs={"pk": self.pk})

    def building_price_per_m2(self):
        """Price per m² of building floor area, in yen. None if unparseable."""
        area = parse_area_to_m2(self.building_area)
        if not area or not self.price:
            return None
        return (self.price * 10000) / area

    def price_per_sqm_comparison(self):
        """Where this property's building ¥/m² sits within its prefecture.

        Returns a dict with a value band ("Great value" / "Around average" /
        "Premium") and context numbers, or None when this property has no
        parseable ¥/m² or the area has too few comparable listings to mean
        anything (see MIN_AREA_SAMPLE_FOR_COMPARISON).
        """
        own = self.building_price_per_m2()
        if not own:
            return None

        area = self.get_location_for_front()
        # Pull price + building_area for every front-visible listing in the
        # same prefecture in one query and parse ¥/m² in Python — building_area
        # is free text, so it can't be aggregated in SQL.
        rows = Property.objects.filter(
            location__icontains=area, show_in_front=True
        ).values_list("price", "building_area")

        values = []
        for price, building_area in rows:
            sqm = parse_area_to_m2(building_area)
            if sqm and price:
                values.append((price * 10000) / sqm)

        if len(values) < MIN_AREA_SAMPLE_FOR_COMPARISON:
            return None

        values.sort()
        n = len(values)
        # Percentile = share of nearby listings that are cheaper per m².
        below = sum(1 for v in values if v < own)
        percentile = round(below / n * 100)

        if percentile < 33:
            band, label = "great_value", "Great value"
        elif percentile < 67:
            band, label = "average", "Around average"
        else:
            band, label = "premium", "Premium"

        def at(p):
            return values[min(int(p / 100 * n), n - 1)]

        def usd(yen):
            # Whole-dollar USD; convert_yen_to_usd keeps cents, which look
            # noisy on a per-m² figure. Same 0.007 rate as the rest of the site.
            return f"${round(yen * 0.007):,}"

        return {
            "area": area,
            "sample_size": n,
            "value_per_m2": round(own),
            "area_low": round(at(25)),
            "area_median": round(at(50)),
            "area_high": round(at(75)),
            "percentile": percentile,
            "band": band,
            "band_label": label,
            "value_per_m2_display": f"{usd(own)}/m²",
            "area_range_display": f"{usd(at(25))} – {usd(at(75))}/m²",
            "area_low_display": f"{usd(at(25))}/m²",
            "area_high_display": f"{usd(at(75))}/m²",
        }


class PropertyImage(models.Model):
    property = models.ForeignKey(
        Property, related_name="images", on_delete=models.CASCADE, null=True
    )
    file = models.FileField(
        upload_to="property_images/", max_length=254, null=True, blank=True
    )
    show_in_front = models.BooleanField(default=True)
    first_image = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Property Image"
        verbose_name_plural = "Property Images"

    def __str__(self):
        return f"{self.property.title} - Image"
