from django.db import models
from inventory.utils import convert_price_string, convert_yen_to_usd, infer_location



class Property(models.Model):
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
    description = models.TextField(default="", blank=True) #remarks
    construction = models.CharField(max_length=255, blank=True)
    show_in_front = models.BooleanField(default=True)
    featured = models.BooleanField(default=False)
    premium = models.BooleanField(default=False)

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
    
    def property_has_any_image(self):
        return self.images.exists()
    
    def get_ordered_images(self):
        return self.images.order_by('first_image', '-id')
    
    def get_location_for_front(self):
        return infer_location(self.location)
    

class PropertyImage(models.Model):
    property = models.ForeignKey(Property, related_name='images', on_delete=models.CASCADE, null=True)
    file = models.FileField(upload_to="property_images/", max_length=254, null=True, blank=True)
    show_in_front = models.BooleanField(default=True)
    first_image = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Property Image"
        verbose_name_plural = "Property Images"
        
    def __str__(self):
        return f"{self.property.title} - Image"
    

