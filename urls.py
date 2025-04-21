from django.contrib import admin
from front import views as front_views
from membership import views as membership_views
from django.urls import path, include

urlpatterns = [
    path("", front_views.display_home, name="home"),
    path("p/<int:pk>/", front_views.property_detail, name="property_detail"),
    path("api/send-booking-confirmation", front_views.send_booking_confirmation, name="send_booking_confirmation"),
    path('authenticate/<int:pk>/', membership_views.show_authenticate_page, name='authenticate'),
    path('register-via-email/<int:pk>/', membership_views.register_via_email, name='register_via_email'),
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('contact-seller/<int:pk>/', front_views.contact_seller, name='contact_seller')
]
