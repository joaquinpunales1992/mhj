from django.contrib import admin
from front import views as front_views
from membership import views as membership_views
from django.urls import path

urlpatterns = [
    path('authenticate', membership_views.show_authenticate_page, name='authenticate')
    # path('contact-seller/<int:seller_id>/', front_views.contact_seller, name='contact_seller'),
]
