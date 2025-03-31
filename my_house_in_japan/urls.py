from django.contrib import admin
from my_house_in_japan.front.views import display_home
from django.urls import path

urlpatterns = [
    path("", display_home, name="home"),
    path("admin/", admin.site.urls),
]
