import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# backend/.env is the local runserver file (MinIO, LiveKit on localhost).
# The repo-root .env is what Compose interpolates. Process environment
# already set by Compose must win, so neither file overrides existing vars.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

_configured_hosts = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
if "*" in _configured_hosts:
    ALLOWED_HOSTS = ["*"]
else:
    # LiveKit webhooks and the Egress recording page call this process
    # as host.docker.internal from inside Docker.
    ALLOWED_HOSTS = list(dict.fromkeys([*_configured_hosts, "host.docker.internal"]))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.meetings.apps.MeetingsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# Slide PDFs are uploaded as one multipart body, then rendered in the browser.
DATA_UPLOAD_MAX_MEMORY_SIZE = 26 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 26 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

APPEND_SLASH = False

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS + [
    origin
    for origin in ["http://localhost", "http://127.0.0.1"]
    if origin not in CORS_ALLOWED_ORIGINS
]

# LiveKit env names match the official SDKs:
# LIVEKIT_URL          — client connection URL (returned as TokenSource server_url)
# LIVEKIT_API_KEY      — JWT issuer
# LIVEKIT_API_SECRET   — JWT signing secret
# LIVEKIT_API_URL      — HTTP URL Django uses for Room/Egress APIs inside Docker
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL") or os.environ.get(
    "LIVEKIT_PUBLIC_URL", "ws://localhost:7880"
)
LIVEKIT_API_URL = os.environ.get("LIVEKIT_API_URL") or os.environ.get(
    "LIVEKIT_INTERNAL_URL", "http://localhost:7880"
)

S3_KEY_ID = os.environ.get("S3_KEY_ID", "")
S3_KEY_SECRET = os.environ.get("S3_KEY_SECRET", "")
# Endpoint Egress and Django use to upload and read. Inside Compose this is http://minio:9000.
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
# Host the browser can open for a presigned download. Leave empty to stream through Django.
S3_PUBLIC_ENDPOINT = os.environ.get("S3_PUBLIC_ENDPOINT", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# Host folder where Egress writes MP4s. Compose mounts it at /out in the
# egress container. Leave empty to upload to S3/MinIO instead.
RECORDING_LOCAL_DIR = os.environ.get("RECORDING_LOCAL_DIR", "").strip()
# Page Chrome loads inside Egress. It must be reachable from that container.
# This is the meeting stage (people and slides, no control bar) served by Next.
# It records even when nobody publishes a camera or a microphone. Empty uses
# Egress's built-in template, which waits for a published track and then never starts.
RECORDING_TEMPLATE_URL = os.environ.get(
    "RECORDING_TEMPLATE_URL",
    "http://host.docker.internal:3000/egress",
).strip()
# Presigned recording links stay valid long enough to play the file.
RECORDING_URL_TTL_SECONDS = int(os.environ.get("RECORDING_URL_TTL_SECONDS", "3600"))

PARTICIPANT_COOKIE = "random-participant-postfix"
