"""Posting a reel to TikTok.

The video is the same file the Instagram reel pipeline already builds — 9:16,
MP4, price and place burnt in. What differs is everything around it.

THREE THINGS THAT ARE NOT LIKE META.

1. The audit. TikTok's own words: "All content posted by unaudited clients will
   be restricted to private viewing mode." Until the app passes review, a post
   from here is visible to nobody but the account owner, and asking for a public
   privacy level is refused outright. So TIKTOK_PRIVACY_LEVEL defaults to
   SELF_ONLY: the first runs work, they are simply private, and one constant
   changes when the audit clears.

2. The refresh token rotates. An access token lasts 24 hours; the refresh token
   lasts a year, but each refresh MAY return a new one and "you must use the
   newly-returned token". Lose a rotated token and the only way back is
   authorising by hand in a browser. Everything here writes the whole token file
   back on every refresh, atomically, and treats that write as the part that
   must not fail.

3. There is no upload-by-URL without verifying a domain in the developer
   portal. We PUT the bytes instead, which also means the reel never has to be
   publicly reachable to be posted — unlike the Instagram path, which needs the
   file served off the site first.
"""

import json
import logging
import os
import time

import requests
from django.conf import settings

from social.constants import (
    TIKTOK_DISABLE_COMMENT,
    TIKTOK_DISABLE_DUET,
    TIKTOK_DISABLE_STITCH,
    TIKTOK_PRIVACY_LEVEL,
    TIKTOK_SCOPES,
    TIKTOK_TIMEOUT,
)

logger = logging.getLogger(__name__)

API_ROOT = "https://open.tiktokapis.com/v2"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"

# TikTok takes one chunk up to 64MB. A 540x960 reel of a dozen seconds is a few
# megabytes, so this is a guard rather than a code path: if a reel ever arrives
# bigger than this, chunking has to be written and it is better to say so than
# to send a truncated video.
MAX_SINGLE_CHUNK = 64 * 1024 * 1024


class TikTokError(RuntimeError):
    """A TikTok API call that failed in a way worth reading.

    Carries the error code TikTok returned, because the codes are the useful
    part: `unaudited_client_can_only_post_to_private_accounts` and
    `spam_risk_too_many_posts` need entirely different responses from you, and
    both arrive as an HTTP 200.
    """

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _token_path():
    """Absolute, so cron and the web app agree on which file this is.

    The Meta token beside it is opened by a bare relative name, which works only
    because the cron entries happen to cd into the project first.
    """
    return getattr(
        settings, "TIKTOK_TOKEN_FILE",
        os.path.join(str(settings.BASE_DIR), "tiktok_token.json"),
    )


def load_tokens():
    try:
        with open(_token_path()) as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError):
        return {}


def save_tokens(tokens):
    """Write the token file atomically.

    A half-written token file is not a recoverable state: the refresh token in
    it is the only thing standing between this and re-authorising by hand in a
    browser. Write a temp file, then rename — rename is atomic, so the file is
    either the old tokens or the new ones and never half of each.
    """
    path = _token_path()
    temporary = f"{path}.tmp"
    with open(temporary, "w") as handle:
        json.dump(tokens, handle, indent=2)
    os.replace(temporary, path)


def _credentials():
    key = getattr(settings, "TIKTOK_CLIENT_KEY", "")
    secret = getattr(settings, "TIKTOK_CLIENT_SECRET", "")
    if not key or not secret:
        raise TikTokError(
            "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are not set in .env"
        )
    return key, secret


def _oauth(payload):
    key, secret = _credentials()
    payload = {"client_key": key, "client_secret": secret, **payload}
    response = requests.post(
        f"{API_ROOT}/oauth/token/",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIKTOK_TIMEOUT,
    )
    body = response.json()
    if response.status_code != 200 or "access_token" not in body:
        raise TikTokError(
            f"Token request failed (HTTP {response.status_code}): {response.text}",
            code=body.get("error", ""),
        )
    # TikTok returns a lifetime; what a later run needs is a deadline. Stamped
    # here so both the initial exchange and every refresh carry it.
    body["expires_at"] = time.time() + int(body.get("expires_in") or 0)
    return body


def authorize_url(redirect_uri, state="mhj"):
    """The URL to open once in a browser to grant the app access."""
    key, _ = _credentials()
    from urllib.parse import urlencode

    return f"{AUTHORIZE_URL}?" + urlencode({
        "client_key": key,
        "scope": ",".join(TIKTOK_SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    })


def exchange_code(code, redirect_uri):
    """Turn the one-time ?code= from the browser into stored tokens."""
    tokens = _oauth({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    })
    save_tokens(tokens)
    logger.info("TikTok tokens stored for open_id %s", tokens.get("open_id", "?"))
    return tokens


def refresh_access_token():
    """Renew the access token, keeping whatever refresh token comes back.

    The returned refresh token may differ from the one sent. Saving the whole
    response rather than merging fields is deliberate: it is the only way to be
    sure a rotated token is never dropped in favour of the one we already had.
    """
    stored = load_tokens()
    refresh_token = stored.get("refresh_token")
    if not refresh_token:
        raise TikTokError(
            "No TikTok refresh token stored. Run `manage.py tiktok_auth` first."
        )

    tokens = _oauth({"grant_type": "refresh_token", "refresh_token": refresh_token})
    if tokens.get("refresh_token") != refresh_token:
        logger.info("TikTok rotated the refresh token; storing the new one.")
    save_tokens(tokens)
    return tokens["access_token"]


def get_fresh_token():
    """A usable access token, refreshed when the stored one has expired.

    Expiry is tracked rather than discovered: finding out by being refused costs
    a failed post, and the run happens once a day where a refresh costs one
    request.
    """
    stored = load_tokens()
    token = stored.get("access_token")
    # A minute of slack, so a token that expires mid-upload is refreshed first.
    if token and stored.get("expires_at", 0) > time.time() + 60:
        return token
    return refresh_access_token()


def _call(path, token, payload):
    response = requests.post(
        f"{API_ROOT}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=payload,
        timeout=TIKTOK_TIMEOUT,
    )
    body = response.json() if response.content else {}
    error = body.get("error") or {}
    code = error.get("code", "")
    # TikTok answers 200 with an error object inside, so the status code alone
    # says nothing about whether this worked.
    if response.status_code != 200 or code not in ("", "ok"):
        raise TikTokError(
            f"{path} failed: {code or response.status_code} "
            f"{error.get('message') or response.text}",
            code=code,
        )
    return body.get("data") or {}


def query_creator_info(token):
    """Who we are about to post as, and what that account allows.

    Required before a direct post, and useful on its own: it is the only call
    that says which privacy levels the account can actually use, and TikTok
    refuses a post whose privacy_level is not one of them.
    """
    return _call("/post/publish/creator_info/query/", token, {})


def _post_info(caption, creator):
    """The post settings, reconciled with what the account permits.

    An account that has comments off must be posted to with disable_comment
    set: sending the opposite is not a request TikTok argues with, it is a
    rejected post.
    """
    allowed = creator.get("privacy_level_options") or []
    privacy = TIKTOK_PRIVACY_LEVEL
    if allowed and privacy not in allowed:
        # SELF_ONLY is in every account's options, so this is a real fallback
        # rather than a different way to fail.
        logger.warning(
            "TikTok will not accept privacy level %s for this account "
            "(allowed: %s); posting privately instead.", privacy, allowed
        )
        privacy = "SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0]

    return {
        # TikTok counts the title in UTF-16 runes and caps it at 2200. Our
        # captions are far shorter, but a caption is generated text and the cap
        # is cheaper to respect than to discover.
        "title": (caption or "")[:2200],
        "privacy_level": privacy,
        "disable_comment": TIKTOK_DISABLE_COMMENT or bool(creator.get("comment_disabled")),
        "disable_duet": TIKTOK_DISABLE_DUET or bool(creator.get("duet_disabled")),
        "disable_stitch": TIKTOK_DISABLE_STITCH or bool(creator.get("stitch_disabled")),
    }


def _upload(upload_url, filepath, size):
    with open(filepath, "rb") as handle:
        response = requests.put(
            upload_url,
            data=handle,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
                # Inclusive byte range, so the last byte is size - 1. An
                # off-by-one here is accepted and then fails at publish time,
                # which is a long way from the cause.
                "Content-Range": f"bytes 0-{size - 1}/{size}",
            },
            timeout=TIKTOK_TIMEOUT,
        )
    if response.status_code not in (200, 201, 202):
        raise TikTokError(
            f"Video upload failed (HTTP {response.status_code}): {response.text}"
        )


def publish_video(filepath, caption):
    """Post `filepath` to TikTok. Returns the publish id.

    Raises TikTokError with the code TikTok gave, so a caller can tell a banned
    account from a daily cap from an app that has not been audited yet.
    """
    size = os.path.getsize(filepath)
    if size <= 0:
        raise TikTokError(f"{filepath} is empty; nothing to post")
    if size > MAX_SINGLE_CHUNK:
        raise TikTokError(
            f"{filepath} is {size} bytes, over the {MAX_SINGLE_CHUNK}-byte single "
            "chunk limit — chunked upload is not implemented"
        )

    token = get_fresh_token()
    creator = query_creator_info(token)
    logger.info(
        "Posting to TikTok as %s (privacy options: %s)",
        creator.get("creator_username", "?"),
        creator.get("privacy_level_options"),
    )

    data = _call("/post/publish/video/init/", token, {
        "post_info": _post_info(caption, creator),
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    })

    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url or not publish_id:
        raise TikTokError(f"init returned no upload target: {data}")

    _upload(upload_url, filepath, size)
    logger.info("Uploaded %s bytes to TikTok, publish_id=%s", size, publish_id)
    return publish_id


def fetch_status(publish_id):
    """Where a publish got to. TikTok processes the video after the upload."""
    return _call(
        "/post/publish/status/fetch/", get_fresh_token(), {"publish_id": publish_id}
    )
