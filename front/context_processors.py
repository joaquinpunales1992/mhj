"""Template context available everywhere.

Exists mainly for the allauth pages: they're rendered by allauth's own views,
so there's no project view to pass these in, and hardcoding "50 homes" into the
signup copy would silently drift the moment the setting changed.
"""

from django.conf import settings


def site_settings(request):
    return {
        "free_limit": settings.VIEW_LIMIT_FREE,
        "anon_limit": settings.VIEW_LIMIT_ANONYMOUS,
        "pro_price": settings.PRO_PRICE_LABEL,
    }
