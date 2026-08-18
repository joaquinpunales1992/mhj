import os
from pathlib import Path
import environ

env = environ.Env()
# Load .env by ABSOLUTE path (next to this settings file). A relative ".env"
# only works when the process CWD is the project dir (e.g. manage.py / cron) —
# under Passenger the web process has a different CWD, so .env silently wasn't
# loaded and env-backed settings (Google OAuth, etc.) came up empty on the web.
environ.Env.read_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# This key signs session cookies and password-reset tokens. The default below
# is the auto-generated one that has been committed to the repo since day one —
# anyone who can read the repo can forge a session with it. Set SECRET_KEY in
# the server .env to a fresh 50+ character random string.
#
# Rotating it logs everyone out, so do it NOW while the account count is
# effectively zero rather than later. Generate one with:
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
SECRET_KEY = env(
    "SECRET_KEY",
    default="django-insecure-wl@vypt@4fcmd!9ix!ez=9**e=+^o8cqley39pziipsq6@ouu6",
)

# SECURITY WARNING: don't run with debug turned on in production!
#
# Now env-driven so the server can turn it off without a code change. The
# default stays True purely to preserve existing behaviour on deploy — set
# DEBUG=False in the server .env as soon as you've confirmed static files
# still serve (whitenoise handles them, so they should).
#
# Two reasons this matters here specifically:
#   - Debug error pages expose stack traces, SQL and template context to any
#     visitor who triggers an exception.
#   - With DEBUG=True Django retains every SQL query for the life of the
#     process. On a low-RAM VPS that is an unbounded leak in the web worker.
DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = [
    "127.0.0.1",
    "www.akiyainjapan.com",
    "akiyainjapan.com",
    "myhouseinjapan.simplifiedbites.com",
    "www.myhouseinjapan.simplifiedbites.com",
]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_social_share",
    "compressor",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "front",
    "scrapper",
    "inventory",
    "social",
    "membership",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # whitenoise must come right after SecurityMiddleware so it can serve
    # collected static files (STATIC_ROOT) directly via WSGI on shared
    # hosting where the web server can't be configured to alias /static/.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# HTTPS / cookie hardening. All env-driven and defaulting OFF so this deploy
# changes nothing; turn them on in the server .env once verified.
#
# Now that the site has real accounts (and soon subscriptions), a session
# cookie sent over plain HTTP is a session anyone on the network can steal.
# Recommended on the server, in this order:
#   SESSION_COOKIE_SECURE=True   — safe immediately if the site is HTTPS-only
#   CSRF_COOKIE_SECURE=True      — likewise
#   SECURE_SSL_REDIRECT=True     — verify no redirect loop first; behind a
#                                  proxy it needs the forwarded-proto header
#                                  below, or Django can't tell it's on HTTPS.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
if env.bool("USE_FORWARDED_PROTO", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ROOT_URLCONF = "urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # front/templates is listed explicitly so it is searched BEFORE the
        # installed apps. allauth ships its own account/*.html and appears
        # earlier in INSTALLED_APPS, so with APP_DIRS alone its unstyled
        # defaults win and our branded signup/login pages never render.
        "DIRS": [
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "front", "templates"
            )
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "front.context_processors.site_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

# Trimmed to the two rules that earn their friction. These accounts hold saved
# searches and favourites, not payment details or personal data, so four
# validators' worth of hoops on a free signup costs more in abandoned
# registrations than it buys in security. Length still blocks trivially weak
# passwords, and the common-password list blocks the ones that actually get
# credential-stuffed. Dropped: UserAttributeSimilarity (rejects passwords that
# merely resemble the email) and NumericPassword (already covered by the common
# list for anything short and obvious).
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
]

# EMAIL SETTINGS — Namecheap Private Email (hello@akiyainjapan.com)
# All values are env-overridable; defaults target the Private Email mailbox.
# Set EMAIL_HOST_PASSWORD (the mailbox password) in the server .env. If it's
# missing, fall back to the console backend so a misconfiguration logs the
# message instead of raising on every send.
EMAIL_HOST = env("EMAIL_HOST", default="mail.privateemail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="hello@akiyainjapan.com")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="My Akiya in Japan <hello@akiyainjapan.com>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL

if EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# LEAD ROUTING
# Who gets told when someone raises their hand. These were hardcoded to a
# personal Gmail inside membership.utils, which meant every lead had to be
# manually forwarded to the agent and nothing was tracked.
#
# LEAD_NOTIFICATION_EMAILS — internal alerts (you).
# AGENT_NOTIFICATION_EMAILS — the licensed agent(s) who take the handover.
#   Leave EMPTY until there's a written referral agreement in place: sending
#   free leads is what produced a 0% close rate. Nothing is sent to the agent
#   automatically; referral mail is only ever triggered explicitly from the
#   admin, so this list being populated is not on its own a handover.
LEAD_NOTIFICATION_EMAILS = env.list(
    "LEAD_NOTIFICATION_EMAILS", default=["joaquinpunales@gmail.com"]
)
AGENT_NOTIFICATION_EMAILS = env.list("AGENT_NOTIFICATION_EMAILS", default=[])

# METERED ACCESS
# How many distinct properties each tier may open before the detail locks.
# Photos, price, floor plan and location are never withheld — see
# membership.metering.LOCKED_FIELDS. Crawlers are exempt entirely; metering
# Googlebot would cost the search traffic these pages exist to earn.
# Set VIEW_LIMIT_FREE to 0 to give members unlimited views.
VIEW_LIMIT_ANONYMOUS = env.int("VIEW_LIMIT_ANONYMOUS", default=5)
VIEW_LIMIT_FREE = env.int("VIEW_LIMIT_FREE", default=25)

# PRO SUBSCRIPTION (PayPal)
# Create a Product and a Plan in the PayPal dashboard, then put the plan id
# here. Leave PAYPAL_PLAN_ID empty and the upgrade page shows a waitlist
# instead of a live subscribe button, so the flow is safe to deploy before
# billing is configured.
PAYPAL_CLIENT_ID = env("PAYPAL_CLIENT_ID", default="")
PAYPAL_CLIENT_SECRET = env("PAYPAL_CLIENT_SECRET", default="")
PAYPAL_PLAN_ID = env("PAYPAL_PLAN_ID", default="")
PAYPAL_WEBHOOK_ID = env("PAYPAL_WEBHOOK_ID", default="")
PAYPAL_ENVIRONMENT = env("PAYPAL_ENVIRONMENT", default="sandbox")  # or "live"
# NOTE: never start this with "$" — django-environ resolves a leading "$" as a
# variable reference and recurses until the stack overflows.
PRO_PRICE_LABEL = env("PRO_PRICE_LABEL", default="US$10 / month")

# Paid consultation booking link (Cal.com event type with PayPal attached).
# When empty the consultation page and the auto-responder omit the booking
# call-to-action rather than rendering a dead link.
CONSULT_BOOKING_URL = env("CONSULT_BOOKING_URL", default="")
# NOTE: never let this value START with "$" — django-environ reads a leading
# "$" as a variable-proxy reference and recurses until the stack overflows.
# "US$25" is fine; "$25" hard-crashes settings import at startup.
CONSULT_PRICE_LABEL = env("CONSULT_PRICE_LABEL", default="US$25")

# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Django All Auth
# Google OAuth credentials come from the environment (.env) so secrets stay out
# of source control. Create them in Google Cloud Console (OAuth client ID, type
# "Web application") and set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_SECRET in the
# server's .env. Authorized redirect URI must be:
#   https://<your-domain>/accounts/google/login/callback/
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_SECRET = env("GOOGLE_OAUTH_SECRET", default="")

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "secret": GOOGLE_OAUTH_SECRET,
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}

SOCIALACCOUNT_ADAPTER = "membership.adapter.SocialAccountAdapter"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_LOGOUT_ON_GET = True
SOCIALACCOUNT_STORE_TOKENS = False
LOGIN_REDIRECT_URL = "/"

LOGIN_URL = "/accounts/login/"


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "myhouseinjapan/static/",
]

STATIC_ROOT = BASE_DIR / "my_house_in_japan/static"

COMPRESS_ROOT = STATIC_ROOT
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "compressor.finders.CompressorFinder",
]

MEDIA_ROOT = os.path.join(BASE_DIR, "myhouseinjapan/media")
MEDIA_URL = "/media/"

# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


PROPERTIES_TO_DISPLAY = 60          # legacy — kept for filter_properties/404 view
# 12 rather than 24: each card pulls a full-size original from suumo.jp
# (130-250KB each), so the per-page count is the single biggest lever on how many
# bytes the home page costs — 24 cards was ~2.8MB of images. Halving it halves
# that. Once thumbnails are served instead of originals this can go back up.
PROPERTIES_PER_PAGE = 12            # per-page count for the paginated home grid

HUGGING_FACE_AI_ENDPOINT_URL = ""
HUGGING_FACE_AI_TOKEN = ""

CEREBRAS_API_KEY = env("CEREBRAS_API_KEY", default="")
