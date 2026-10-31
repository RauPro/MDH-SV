"""Pure source identity and text normalization; no DNS or collection adapters."""
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from .publishers import NEWS_HOSTS

def normalize(value):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', value.lower()) if not unicodedata.combining(c))


def canonical_url(value, allowed_hosts=NEWS_HOSTS):
    if not isinstance(value, str) or any(ord(c) < 32 or c in '<>\\' for c in value):
        raise ValueError('Invalid source URL')
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.hostname not in allowed_hosts or parts.username or parts.password or parts.port:
        raise ValueError('Source URL outside approved HTTPS hosts')
    if any(not (key.startswith('utm_') or key in ('fbclid', 'gclid')) for key, _ in parse_qsl(parts.query)):
        raise ValueError('Unexpected source query parameters')
    if len(parts.path) < 2:
        raise ValueError('Article URL cannot be a homepage')
    return urlunsplit(('https', parts.hostname, parts.path, '', ''))
