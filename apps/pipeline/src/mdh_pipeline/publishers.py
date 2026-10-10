"""Explicit publisher identities shared by collection, extraction and publication.

Being listed permits source processing, never automatic approval of its claims.
Only observed HTTPS hosts are listed; subdomains are not implicitly trusted.
"""

SOURCES = (
    {"id": "co-latino", "name": "Diario Co Latino", "kind": "wordpress", "endpoint": "https://www.diariocolatino.com/wp-json/wp/v2/posts", "hosts": ["www.diariocolatino.com", "diariocolatino.com"]},
    {"id": "gatoencerrado", "name": "GatoEncerrado", "kind": "wordpress", "endpoint": "https://gatoencerrado.news/wp-json/wp/v2/posts", "hosts": ["gatoencerrado.news"]},
    {"id": "factum", "name": "Revista Factum", "kind": "wordpress", "endpoint": "https://www.revistafactum.com/wp-json/wp/v2/posts", "hosts": ["www.revistafactum.com", "revistafactum.com"]},
)

NEWS_HOSTS = frozenset(host for source in SOURCES for host in source["hosts"])
