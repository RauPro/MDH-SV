"""Best-effort public-source archiving. A timeout or save response is not a snapshot."""
import hashlib
import re
from urllib.parse import urlparse
import requests

DEFAULT_USER_AGENT = 'MDH-SV/2.0 (+https://github.com/RauPro/MDH-SV)'


class EvidenceArchiver:
    def __init__(self, user_agent=DEFAULT_USER_AGENT, timeout_seconds=10):
        self.timeout = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent})

    @staticmethod
    def compute_sha256(raw_content):
        return hashlib.sha256(raw_content.encode('utf-8')).hexdigest()

    @staticmethod
    def _snapshot(value, original):
        if not isinstance(value, str): return None
        value = value.replace('http://web.archive.org/', 'https://web.archive.org/', 1)
        match = re.fullmatch(r'https://web\.archive\.org/web/\d{14}/(https?://.+)', value)
        return value if match and match.group(1) == original else None

    def check_existing_snapshot(self, url):
        try:
            response = self.session.get('https://archive.org/wayback/available', params={'url': url}, timeout=self.timeout)
            response.raise_for_status()
            closest = response.json().get('archived_snapshots', {}).get('closest', {})
            if closest.get('available'):
                return self._snapshot(closest.get('url'), url)
        except (requests.RequestException, ValueError, TypeError):
            pass
        return None

    def request_wayback_preservation(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            return None, 'Se requiere una fuente HTTPS pública sin credenciales ni parámetros'
        existing = self.check_existing_snapshot(url)
        if existing:
            return existing, 'Snapshot localizado; su contenido requiere revisión'
        try:
            response = self.session.get('https://web.archive.org/save/' + url, timeout=self.timeout)
            response.raise_for_status()
            snapshot = self._snapshot(response.url, url)
            if snapshot:
                return snapshot, 'Snapshot localizado; su contenido requiere revisión'
            return None, 'Solicitud enviada; no se confirmó un snapshot'
        except requests.RequestException:
            return None, 'No se confirmó la preservación'
