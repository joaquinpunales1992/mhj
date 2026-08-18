from django.contrib import admin
from front import views as front_views
from membership.utils import notify_user_expressed_interest
from membership import views as membership_views
from membership import consultations as consultation_views
from membership.paypal import webhook as paypal_webhook
from django.urls import path, include
from front import sitemap
from django.conf import settings
from django.conf.urls.static import static

handler404 = "front.views.redirect_404_view"

urlpatterns = [
    path("", front_views.display_home, name="home"),
    path(
        "houses-in-<str:region>/",
        front_views.region_listing,
        name="region_listing",
    ),
    path("about/", front_views.about, name="about"),
    path("how-to-buy/", front_views.how_to_buy, name="how_to_buy"),
    path("faqs/", front_views.faqs, name="faqs"),
    path("consultation/", front_views.consultation, name="consultation"),
    # Booking a paid call: hold + PayPal order, then PayPal's two redirects back.
    path(
        "consultation/book",
        consultation_views.book_consultation,
        name="book_consultation",
    ),
    path(
        "consultation/booked",
        consultation_views.consultation_return,
        name="consultation_return",
    ),
    path(
        "consultation/cancelled",
        consultation_views.consultation_cancelled,
        name="consultation_cancelled",
    ),
    path("pricing/", front_views.pricing, name="pricing"),
    path("map/", front_views.map_view, name="map"),
    path(
        "api/map-properties.json",
        front_views.map_properties_json,
        name="map_properties_json",
    ),
    path(
        "api/map-cards.json",
        front_views.map_property_cards_json,
        name="map_property_cards_json",
    ),
    path(
        "filter/<str:category>/",
        front_views.filter_properties,
        name="filter_properties",
    ),
    path(
        "sitemap.xml",
        sitemap.display_sitemaps,
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path(
        "api/send-booking-confirmation",
        front_views.send_booking_confirmation,
        name="send_booking_confirmation",
    ),
    path(
        "api/update-like-count/<int:property_id>/",
        front_views.update_like_count,
        name="update_like_count",
    ),
    path(
        "api/submit-premium-request",
        front_views.submit_premium_request,
        name="submit_premium_request",
    ),
    path(
        "api/submit-interest",
        front_views.submit_interest_request,
        name="submit_interest_request",
    ),
    path(
        "authenticate/<int:pk>/<str:redirect_to_premium>/",
        membership_views.show_authenticate_page,
        name="authenticate",
    ),
    path(
        "register-via-email/<int:pk>/<str:redirect_to_premium>/",
        membership_views.register_via_email,
        name="register_via_email",
    ),
    # Legacy $4.99 page. Kept as a permanent redirect because it is in the
    # sitemap; the template and view are gone.
    path(
        "upgrade-premium/",
        front_views.legacy_premium_redirect,
        name="upgrade_premium",
    ),
    path(
        "approved-membership-payment/",
        membership_views.approved_membership_payment,
        name="approved_membership_payment",
    ),
    path("saved/", membership_views.saved_view, name="saved"),
    path("pro/", membership_views.upgrade_pro, name="upgrade_pro"),
    path(
        "api/register-subscription",
        membership_views.register_subscription,
        name="register_subscription",
    ),
    path("api/paypal-webhook", paypal_webhook, name="paypal_webhook"),
    path(
        "api/toggle-saved-property",
        membership_views.toggle_saved_property,
        name="toggle_saved_property",
    ),
    path("api/save-search", membership_views.save_search, name="save_search"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path(
        "japanese-houses/<int:pk>/<str:user_just_registered>/",
        front_views.property_detail,
        name="property_detail",
    ),
    path(
        "japanese-houses/<int:pk>/", front_views.property_detail, name="property_detail"
    ),
    # Redirect for legacy URLs
    path(
        "contact-seller/<int:pk>/<str:user_just_registered>/",
        front_views.legacy_contact_seller_optional_redirect,
        name="legacy_contact_seller_optional_redirect",
    ),
    path(
        "contact-seller/<int:pk>/",
        front_views.legacy_contact_seller_redirect,
        name="legacy_contact_seller_redirect ",
    ),
    path(
        "notify-user-expressed-interest/",
        notify_user_expressed_interest,
        name="notify_user_expressed_interest",
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
