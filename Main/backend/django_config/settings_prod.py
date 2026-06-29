"""Production settings for the FinGPT backend."""

from django.core.exceptions import ImproperlyConfigured

from .settings import *

DEBUG = False

SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').strip().lower() in ('true', '1', 't')
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'True').strip().lower() in ('true', '1', 't')
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'True').strip().lower() in ('true', '1', 't')
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'True').strip().lower() in ('true', '1', 't')
SECURE_HSTS_PRELOAD = os.getenv('SECURE_HSTS_PRELOAD', 'True').strip().lower() in ('true', '1', 't')
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

DEFAULT_ALLOWED_ORIGINS = [
    "https://www.tradingview.com",
    "https://tradingview.com",
    "https://www.xyzterminal.com",
    "https://xyzterminal.com",
    "https://www.bloomberg.com",
    "https://bloomberg.com",
    "https://finance.yahoo.com",
]

cors_origins_env = os.getenv('CORS_ALLOWED_ORIGINS')
if not cors_origins_env:
    raise ImproperlyConfigured(
        "CORS_ALLOWED_ORIGINS environment variable is required in production. "
        "Set it to a comma-separated list of allowed origins, e.g., "
        "'https://yourdomain.com,chrome-extension://your-extension-id'"
    )

CORS_ALLOWED_ORIGINS = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
CORS_ALLOWED_ORIGINS.extend(DEFAULT_ALLOWED_ORIGINS)
CORS_ALLOWED_ORIGINS = list(set(CORS_ALLOWED_ORIGINS))
CORS_ALLOW_ALL_ORIGINS = False
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

allowed_hosts_env = os.getenv('DJANGO_ALLOWED_HOSTS')
if not allowed_hosts_env or allowed_hosts_env == '*':
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must be explicitly set in production. "
        "Set it to a comma-separated list of allowed hostnames, e.g., "
        "'yourdomain.com,api.yourdomain.com'"
    )
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_env.split(',') if host.strip()]

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY or 'django-insecure' in SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a secure random value in production. "
        "Generate one with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'"
    )

DATABASES = {}

# Counter store for the agent budget (api/agent_budget.py) and django-ratelimit.
# RedisCache provides atomic incr/decr shared across all gunicorn workers, so the
# agent concurrency + daily-run caps are HARD limits (no MAX_ENTRIES cull of
# counters as a file/locmem cache would do). LOCATION comes from REDIS_URL.
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 3600,
    }
}

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATIC_URL = '/static/'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {
            '()': 'api.utils.logging_filters.RequestIdFilter',
        },
    },
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} [{request_id}] {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['request_id'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'api.middleware.memory_tracker': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

required_keys = ['OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY']
missing_keys = [key for key in required_keys if not os.getenv(key)]
if len(missing_keys) == len(required_keys):
    raise ImproperlyConfigured(
        f"At least one API key must be set. Missing: {', '.join(missing_keys)}"
    )
