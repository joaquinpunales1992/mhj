from django.contrib import admin
from front import views as front_views
from membership import views as membership_views
from django.urls import path, include
from front import sitemap 
from django.conf import settings
from django.conf.urls.static import static

handler404 = 'front.views.redirect_404_view'

urlpatterns = [
    path("", front_views.display_home, name="home"),
    path("filter/<str:category>/", front_views.filter_properties, name="filter_properties"),
    path('sitemap.xml', sitemap.display_sitemaps, name='django.contrib.sitemaps.views.sitemap'),
    path("api/send-booking-confirmation", front_views.send_booking_confirmation, name="send_booking_confirmation"),
    path("api/update-like-count/<int:property_id>/", front_views.update_like_count, name="update_like_count"),  
    path("api/submit-premium-request", front_views.submit_premium_request, name="submit_premium_request"),
    path('authenticate/<int:pk>/<str:redirect_to_premium>/', membership_views.show_authenticate_page, name='authenticate'),
    path('register-via-email/<int:pk>/<str:redirect_to_premium>/', membership_views.register_via_email, name='register_via_email'),
    path('upgrade-premium/', membership_views.upgrade_premium, name='upgrade_premium'),
    path('approved-membership-payment/', membership_views.approved_membership_payment, name='approved_membership_payment'),
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('japanese-houses/<int:pk>/<str:user_just_registered>/', front_views.property_detail, name='property_detail'),
    path('japanese-houses/<int:pk>/', front_views.property_detail, name='property_detail_optional'),    
    # Redirect for legacy URLs
    path('contact-seller/<int:pk>/<str:user_just_registered>/', front_views.legacy_contact_seller_optional_redirect, name='legacy_contact_seller_optional_redirect'),
    path('contact-seller/<int:pk>/', front_views.legacy_contact_seller_redirect, name='legacy_contact_seller_redirect '),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
