"""
Django settings for InsightLab — Intelligent Data Analytics and Predictive Intelligence Platform.
"""

import os
import secrets
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def _get_bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean flag from an environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _get_bool_env('DJANGO_DEBUG', default=True)


def _get_secret_key(base_dir: Path, debug_mode: bool) -> str:
    """
    Load SECRET_KEY from environment, or generate a local gitignored dev key
    when running in DEBUG mode so no secret is ever hardcoded in source control.
    """
    env_key = os.environ.get('DJANGO_SECRET_KEY')
    if env_key:
        return env_key

    if not debug_mode:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY environment variable must be set when DEBUG is False.'
        )

    dev_key_file = base_dir / '.dev_secret_key'
    if dev_key_file.exists():
        stored_key = dev_key_file.read_text(encoding='utf-8').strip()
        if stored_key:
            return stored_key

    new_key = secrets.token_urlsafe(50)
    dev_key_file.write_text(new_key, encoding='utf-8')
    return new_key


SECRET_KEY = _get_secret_key(BASE_DIR, DEBUG)

allowed_hosts_env = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_env.split(',') if host.strip()]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Project apps
    'accounts',
    'datasets',
    'forecasting',
    'analytics',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# SQLite initially; compatible with PostgreSQL via environment configuration later.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded datasets and generated report files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Authentication redirects
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
