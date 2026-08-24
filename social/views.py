"""The page a person uses to post a listing to TikTok.

The poster runs from cron and needs no interface. This exists for two reasons
that are not automation.

The first is TikTok's own rules. Direct Post expects the creator to see which
account is about to be posted to and to choose the privacy level from the
options that account actually allows, before anything is published. A cron job
cannot show anyone anything. This page is where that consent happens, and it
sends the choices made here rather than a constant.

The second is that TikTok's app review requires a recording of the integration
working — the interface, the interactions, and the site's real domain. There was
nothing to record.

Staff only. It posts to the site's own TikTok account, so it is a control panel
rather than a feature for visitors.
"""

import importlib
import logging
import os
import secrets
import subprocess
import sys

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from social import tiktok
from social.constants import (
    PRICE_LIMIT_INSTAGRAM,
    TIKTOK_POST_TIMEOUT,
    TIKTOK_PRIVACY_LEVEL,
)
from social.models import SocialPost
from social.queue import select_properties_to_post

# social.utils is NOT imported here. It imports moviepy and numpy at module
# level, and urls.py imports this module, so a module-level import would load
# the entire video stack into every web worker at boot — on a box that has been
# OOM-killed encoding video before. It took the site down with a 503. The two
# functions this module needs are imported inside the views that use them, so
# the cost is paid only by a member of staff opening the TikTok page.

logger = logging.getLogger(__name__)

STATE_SESSION_KEY = "tiktok_oauth_state"


def _redirect_uri(request):
    """The registered redirect, which must match TikTok's app settings exactly.

    Built from the setting rather than from the request: TikTok compares it
    character for character with what the app was registered with, and a request
    arriving on a different host would produce a URL it refuses.
    """
    return settings.TIKTOK_REDIRECT_URI


def _next_property():
    """Whatever the TikTok queue would post next — cheapest first."""
    candidates = select_properties_to_post(
        SocialPost.objects.filter(social_media="tiktok"),
        PRICE_LIMIT_INSTAGRAM,
        limit=1,
    )
    return candidates[0] if candidates else None


@staff_member_required
def dashboard(request):
    """Which account is connected, what would be posted, and the settings.

    The creator lookup is a live call on purpose: the privacy levels an account
    allows are the account's business and can change, and a page showing a
    remembered list would be offering a choice TikTok may refuse.
    """
    # Whether an account is connected is a question about the stored tokens,
    # not about whether a call just succeeded. Conflating the two made the page
    # say "connected" and "no account connected" at once when the token was
    # missing a scope.
    stored = tiktok.load_tokens()
    has_tokens = bool(stored.get("refresh_token"))

    creator = None
    profile = {}
    error = ""
    try:
        token = tiktok.get_fresh_token()
        # Two calls, two scopes, and the page shows both: user.info.basic names
        # and pictures the account, video.publish's creator_info says what that
        # account will let us publish.
        creator = tiktok.query_creator_info(token)
        try:
            profile = tiktok.fetch_user_info(token)
        except Exception as exc:
            # The profile is decoration; being unable to post is the failure.
            logger.warning("TikTok profile lookup failed: %s", exc)
    except tiktok.TikTokError as exc:
        error = str(exc)
    except Exception as exc:  # a network failure is not a broken page
        logger.warning("TikTok creator lookup failed: %s", exc)
        error = str(exc)

    privacy_options = (creator or {}).get("privacy_level_options") or []
    return render(request, "social/tiktok.html", {
        "creator": creator,
        "profile": profile,
        "error": error,
        "has_tokens": has_tokens,
        # What TikTok actually granted, which is not always what was asked for:
        # a token missing video.publish fails every posting call with
        # scope_not_authorized, and nothing else on the page would say why.
        "granted_scope": stored.get("scope", ""),
        "ready": bool(creator),
        "property": _next_property(),
        "privacy_options": privacy_options,
        "privacy_default": (
            TIKTOK_PRIVACY_LEVEL if TIKTOK_PRIVACY_LEVEL in privacy_options
            else (privacy_options[0] if privacy_options else "")
        ),
        "creator_defaults": {
            "disable_comment": bool((creator or {}).get("comment_disabled")),
            "disable_duet": bool((creator or {}).get("duet_disabled")),
            "disable_stitch": bool((creator or {}).get("stitch_disabled")),
        },
        "last_posts": SocialPost.objects.filter(
            social_media="tiktok"
        ).order_by("-datetime")[:5],
    })


@staff_member_required
def connect(request):
    """Send the operator to TikTok to grant video.publish."""
    state = secrets.token_urlsafe(24)
    request.session[STATE_SESSION_KEY] = state
    try:
        url = tiktok.authorize_url(_redirect_uri(request), state=state)
    except tiktok.TikTokError as exc:
        messages.error(request, str(exc))
        return redirect(reverse("tiktok_dashboard"))
    return redirect(url)


@staff_member_required
def callback(request):
    """Where TikTok sends the browser back, with a one-time code.

    The state is checked before the code is spent. Without that, a link someone
    else crafted could make this account authorise a TikTok account we did not
    choose.
    """
    expected = request.session.pop(STATE_SESSION_KEY, None)
    if not expected or request.GET.get("state") != expected:
        messages.error(request, "That authorisation did not come from here.")
        return redirect(reverse("tiktok_dashboard"))

    code = request.GET.get("code")
    if not code:
        # TikTok puts the reason in the query string when the operator declines.
        reason = request.GET.get("error_description") or request.GET.get("error")
        messages.error(request, f"TikTok did not authorise the app: {reason}")
        return redirect(reverse("tiktok_dashboard"))

    try:
        tiktok.exchange_code(code, _redirect_uri(request))
    except tiktok.TikTokError as exc:
        messages.error(request, str(exc))
        return redirect(reverse("tiktok_dashboard"))

    messages.success(request, "TikTok account connected.")
    return redirect(reverse("tiktok_dashboard"))


def _manage_py():
    """manage.py, which lives beside settings.py in this project."""
    settings_module = importlib.import_module(
        os.environ.get("DJANGO_SETTINGS_MODULE", "settings")
    )
    return os.path.join(
        os.path.dirname(os.path.abspath(settings_module.__file__)), "manage.py"
    )


@staff_member_required
@require_POST
def post_now(request):
    """Post the queued listing, in a process of its own.

    Not because posting is slow — it is seconds — but because building the video
    imports moviepy and numpy, and this host runs the site under Passenger,
    which preloads and forks. numpy in a forked worker raises "CPU dispatcher
    tracer already initlized" and takes the process with it; that is how the
    site returned 503. A subprocess gets a clean interpreter, its own memory,
    and cannot damage the worker that spawned it.

    It also means the button and the cron job run exactly the same code path,
    which is worth more than the milliseconds a direct call would save.
    """
    argv = [sys.executable, _manage_py(), "post_on_tiktok"]
    if request.POST.get("privacy_level"):
        argv += ["--privacy-level", request.POST["privacy_level"]]
    for name in ("comment", "duet", "stitch"):
        if request.POST.get(f"disable_{name}"):
            argv.append(f"--disable-{name}")

    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=TIKTOK_POST_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        messages.error(
            request, "The post took too long and was stopped. The encode may "
            "still have finished — check the log before trying again."
        )
        return redirect(reverse("tiktok_dashboard"))

    if result.returncode == 0 and "Posted to TikTok" in (result.stdout or ""):
        messages.success(
            request, "Posted to TikTok. TikTok finishes processing the video "
            "after the upload, so it appears on the profile shortly."
        )
    else:
        # The command already logged the detail; the last line is the summary a
        # person can act on without opening the log.
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        messages.error(
            request,
            f"Nothing was posted. {detail[-1] if detail else 'See the log.'}"
        )
    return redirect(reverse("tiktok_dashboard"))
