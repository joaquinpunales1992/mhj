from django.contrib import admin
from my_house_in_japan.front import views as front_views
from my_house_in_japan.membership import views as membership_views
from django.urls import path

urlpatterns = [
    path('authenticate', membership_views.show_authenticate_page, name='authenticate')
    # path('contact-seller/<int:seller_id>/', front_views.contact_seller, name='contact_seller'),
]
