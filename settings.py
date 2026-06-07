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
SECRET_KEY = "django-insecure-wl@vypt@4fcmd!9ix!ez=9**e=+^o8cqley39pziipsq6@ouu6"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

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

ROOT_URLCONF = "urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
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

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# EMAIL SETTINGS
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp-relay.sendinblue.com"
EMAIL_PORT = 587
EMAIL_HOST_USER = "joaquinpunales@gmail.com"
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = "joaquinpunales@gmail.com"

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
PROPERTIES_PER_PAGE = 24            # new — per-page count for the paginated home grid

HUGGING_FACE_AI_ENDPOINT_URL = ""
HUGGING_FACE_AI_TOKEN = ""

CEREBRAS_API_KEY = env("CEREBRAS_API_KEY", default="")
