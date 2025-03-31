from django.db import models



# 'property_title': google_translator.translate(property_title),
#         'property_price': google_translator.translate(table_data['価格']),
#         'floor_plan': google_translator.translate(table_data['間取り']),
#         'building_area': google_translator.translate(table_data['建物面積']),
#         'land_area': google_translator.translate(table_data['土地面積']),
#         'parking': google_translator.translate(table_data['駐車場']),
#         'building_age': google_translator.translate(table_data['築年月']),
#         'location': google_translator.translate(table_data['所在地']),
#         'traffic': google_translator.translate(table_data['交通']),
#         'building_structure': google_translator.translate(table_data['建物構造']),
#         'road_condition': google_translator.translate(table_data['接道状況']),
#         'setback': google_translator.translate(table_data['セットバック']),
#         'city_planning': google_translator.translate(table_data['都市計画']),
#         'zoning': google_translator.translate(table_data['用途地域']),
#         'land_category': google_translator.translate(table_data['地目']),
#         'building_coverage_ratio': google_translator.translate(table_data['建ぺい率']),
#         'floor_area_ratio': google_translator.translate(table_data['容積率']),
#         'current_status': google_translator.translate(table_data['現況']),
#         'handover': google_translator.translate(table_data['引渡し']),
#         # 'equipment': google_translator.translate(table_data['設備']), TODO:FIX
#         'transaction_type': google_translator.translate(table_data['取引態様']),
#         'remarks': google_translator.translate(table_data['備考']),
#         'image_urls': image_urls

class PropertyImage(models.Model):
    file = models.FileField(upload_to="property_images/", max_length=254, null=True, blank=True)

class Property(models.Model):
    title = models.CharField(max_length=255)
    price = models.PositiveIntegerField()
    floor_plan = models.CharField(max_length=255)
    building_area = models.FloatField()
    land_area = models.FloatField()
    parking = models.CharField(max_length=255)
    construction = models.CharField(max_length=255)
    images = models.ForeignKey(to=PropertyImage, related_name="properties", on_delete=models.CASCADE)
    
    def __str__(self):
        return f"{self.tile}: {self.price}"