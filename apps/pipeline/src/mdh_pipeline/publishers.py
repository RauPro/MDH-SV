"""Explicit publisher identities shared by collection, extraction and publication.

Being listed permits source processing, never automatic approval of its claims.
Only observed HTTPS hosts are listed; subdomains are not implicitly trusted.
"""

SOURCES = (
    {"id": "co-latino", "name": "Diario Co Latino", "kind": "wordpress", "endpoint": "https://www.diariocolatino.com/wp-json/wp/v2/posts", "hosts": ["www.diariocolatino.com", "diariocolatino.com"]},
    {"id": "gatoencerrado", "name": "GatoEncerrado", "kind": "wordpress", "endpoint": "https://gatoencerrado.news/wp-json/wp/v2/posts", "hosts": ["gatoencerrado.news"]},
    {"id": "factum", "name": "Revista Factum", "kind": "wordpress", "endpoint": "https://www.revistafactum.com/wp-json/wp/v2/posts", "hosts": ["www.revistafactum.com", "revistafactum.com"]},
    {"id": "el-faro", "name": "El Faro", "kind": "html_archive",
     "endpoint": "https://www.elfaro.net/templates/elfaro/especiales/2010/index.html",
     "hosts": ["elfaro.net", "www.elfaro.net", "beta.elfaro.net"],
     "archive_urls": ["https://www.elfaro.net/templates/elfaro/especiales/2010/index.html", "https://beta.elfaro.net/"],
     "seed_urls": ["https://beta.elfaro.net/el-salvador/trump-bukele-carcel-ruth-lopez"],
     "notice": "Selección parcial del especial de 2010 y páginas públicas actuales. Algunos enlaces antiguos restringen el acceso; no es una hemeroteca completa."},
    {"id": "el-mundo", "name": "Diario El Mundo", "kind": "html_archive",
     "endpoint": "https://diario.elmundo.sv/nacionales", "hosts": ["diario.elmundo.sv"],
     "archive_urls": ["https://diario.elmundo.sv/nacionales", "https://diario.elmundo.sv/politica"],
     "seed_urls": ["https://diario.elmundo.sv/nacionales/pddh-senala-a-policia-y-ejercito-por-ejecuciones-extrajudiciales",
                   "https://diario.elmundo.sv/nacionales/mas-del-30-de-salvadorenos-aprueba-tortura-y-ejecuciones-extrajudiciales"],
     "notice": "Selección parcial de secciones públicas y enlaces históricos verificados. No se ha identificado un índice histórico completo accesible."},
    {"id": "la-prensa-grafica", "name": "La Prensa Gráfica", "kind": "html_archive",
     "endpoint": "https://www.laprensagrafica.com/sitemap.xml",
     "hosts": ["www.laprensagrafica.com", "laprensagrafica.com"],
     "archive_urls": [], "access": "restricted",
     "notice": "Acceso automatizado restringido: el sitemap devolvió HTTP 403 y robots.txt no permite el agente genérico (consulta 10/10/2026). Pendiente de un canal autorizado; no se elude el bloqueo."},
    {"id": "el-diario-de-hoy", "name": "El Diario de Hoy (archivo)", "kind": "html_archive",
     "endpoint": "https://historico.elsalvador.com/historico/SiteMapHistoricoEscom.xml",
     "hosts": ["historico.elsalvador.com", "www.elsalvador.com", "elsalvador.com"],
     "archive_urls": ["https://historico.elsalvador.com/historico/SiteMapHistoricoEscom.xml",
                      "https://historico.elsalvador.com/historico/SiteMapHistoricoEscom2.xml",
                      "https://historico.elsalvador.com/historico/SiteMapHistoricoEscom3.xml"],
     "seed_urls": ["https://historico.elsalvador.com/historico/965496/organizacion-promovera-juicios-violacion-derechos-humanos.html"],
     "notice": "Recorrido reanudable de los sitemaps públicos del archivo. La fecha se consulta en cada artículo; lastmod no se utiliza como fecha de publicación."},
)

NEWS_HOSTS = frozenset(host for source in SOURCES for host in source["hosts"])
