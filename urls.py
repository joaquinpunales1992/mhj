from django.contrib import admin
from front import views as front_views
from membership import views as membership_views
from django.urls import path, include
from django.views.generic import TemplateView
from front import sitemap 


urlpatterns = [
    path("", front_views.display_home, name="home"),
    path('sitemap.xml', sitemap.display_sitemaps, name='django.contrib.sitemaps.views.sitemap'),
    path("p/<int:pk>/", front_views.property_detail, name="property_detail"),
    path("api/send-booking-confirmation", front_views.send_booking_confirmation, name="send_booking_confirmation"),
    path("api/submit-premium-request", front_views.submit_premium_request, name="submit_premium_request"),
    path('authenticate/<int:pk>/<str:redirect_to_premium>/', membership_views.show_authenticate_page, name='authenticate'),
    path('register-via-email/<int:pk>/<str:redirect_to_premium>/', membership_views.register_via_email, name='register_via_email'),
    path('upgrade-premium/', membership_views.upgrade_premium, name='upgrade_premium'),
    path('approved-membership-payment/', membership_views.approved_membership_payment, name='approved_membership_payment'),
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('contact-seller/<int:pk>/<str:user_just_registered>/', front_views.contact_seller, name='contact_seller')
]
