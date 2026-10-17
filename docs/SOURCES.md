# Registro de fuentes

[`shared/source-registry.json`](../shared/source-registry.json) es el registro de
identidades, hosts exactos y capacidades de las fuentes usadas por la publicación
actual. Python y JavaScript validan el mismo documento antes de derivar sus listas.
Registrar una fuente permite el procesamiento declarado; no acredita sus noticias,
no aprueba una ficha ni concede permisos de escritura al publicador.

## Identidades y capacidades

Cada entrada de `publishers` contiene `id`, `name`, `kind`, `endpoint`, `hosts` y
`feeds`. El ID es estable porque aparece en referencias, estados históricos y
rutas de checkpoints. Los nombres y hosts no se repiten entre entradas. No hay
comodines ni confianza implícita en subdominios, aliases `www` o redirecciones.
Los endpoints deben ser URLs HTTPS explícitas dentro de los hosts de la entrada,
sin credenciales, puerto, consulta, fragmento ni escapes ambiguos.

- `kind: wordpress` conserva la colección por API pública y las referencias con
  identificador del post. No admite opciones propias del archivo HTML.
- `kind: html_archive` añade `archive_urls`, un `notice` de cobertura y,
  opcionalmente, `seed_urls` y `access: public | restricted`.
- `access: restricted` impide declarar feeds, índices o semillas activos. La
  entrada existente de La Prensa Gráfica sigue restringida; este cambio no intenta
  sortear bloqueos ni habilitar nuevas consultas.
- Cada feed declara su URL y sus `consumers`. `private_queue` alimenta la cola
  privada; `legacy_scraper` conserva la configuración del scraper anterior. No
  hay consumidor por defecto. Un consumidor sólo puede tener un feed por medio.
  El Faro mantiene su feed anterior sin incorporarse a la colección privada
  actual, que sigue usando Co Latino, GatoEncerrado y Factum.

Las listas de hosts ajenas a noticias tienen capacidades separadas:

| Campo | Uso |
|---|---|
| `primary_evidence_hosts` | Fuentes institucionales admitidas en fichas; la política de evidencia añade los hosts de noticias sin volver a enumerarlos. |
| `official_statistics_hosts` | Fuentes admitidas en el conjunto público de estadísticas oficiales. |
| `official_statistics_discovery_extra_hosts` | Hosts adicionales de investigación privada de estadísticas; no autorizan publicación. |

Las listas son disjuntas. El host de transparencia de la PNC sigue disponible
para investigación privada y continúa fuera de las fuentes públicas de
estadísticas. Ninguna de estas listas permite registrar un host a partir de un
borrador, un artefacto descargado, un parámetro de consulta o una cabecera HTTP.

## Lectores y límites

[`publishers.py`](../apps/pipeline/src/mdh_pipeline/publishers.py) carga el archivo
relativo al código aprobado, sin usar el directorio actual ni variables de entorno.
Sus proyecciones conservan `SOURCES` y `NEWS_HOSTS`; `registered_feeds()` diferencia
los consumidores. Historia, extracción, selección y exportación siguen usando
esas identidades. El lector rechaza claves JSON repetidas, archivos enlazados,
contenido excesivo y configuraciones desconocidas antes de devolver entradas.

[`source-registry.mjs`](../shared/source-registry.mjs) valida la importación JSON y
expone proyecciones congeladas para navegador, servidor y comprobaciones de datos.
`NEWS_SOURCES`, `PUBLISHER_HOSTS` y `HTML_PUBLISHERS` evitan mantener otra lista en
el parser de noticias. El archivo JSON debe incluirse en el despliegue junto a los
módulos compartidos; no se obtiene del snapshot público de datos.

La configuración no sustituye a los adaptadores: los selectores de texto, las
reglas del archivo histórico de EDH, las comprobaciones DNS y de redirecciones,
los límites de descarga y las reglas editoriales permanecen en sus módulos.
Los scrapers anteriores de sitemaps y búsquedas tienen algoritmos especializados
fuera del pipeline publicado; este registro no los activa ni los moderniza.
`scripts/check_sources.py` comprueba enlaces del catálogo ya validado: una
respuesta HTTP satisfactoria no registra una fuente ni acredita sus afirmaciones.

## Añadir o modificar una fuente

1. Cambiar la entrada del registro en una rama y explicar la procedencia de cada
   host y endpoint. Declarar sólo los consumidores necesarios y las limitaciones
   de acceso conocidas. No incluir credenciales, cuerpos periodísticos ni datos
   de personas.
2. Añadir fixtures sintéticas o mínimas y comprobar identidad, URLs fuera del host,
   redirecciones, fechas, cobertura y límites de contenido. Si el medio requiere
   un adaptador, revisarlo por separado; una entrada de registro no vuelve seguro
   un extractor nuevo.
3. Ejecutar las comprobaciones locales. El corpus compartido demuestra que ambos
   lectores rechazan las mismas configuraciones inválidas. Las pruebas de alta
   sintética comprueban colección, historia, parser web y política de evidencia
   sin consultar una fuente real ni modificar el registro de producción.

   ```sh
   .venv/bin/python -m pytest tests/test_source_registry.py tests/test_history.py tests/test_review_queue.py -q
   node --test tests/source-registry.test.mjs
   npm --prefix apps/web test -- --run src/sourceRegistry.test.ts src/newsArchive.test.ts
   ```

4. Revisar cualquier cambio intencional del inventario y sus pruebas de política.
   Ejecutar después `python3.13 scripts/check.py`. Las comprobaciones de enlaces
   en red son independientes y no se necesitan para ejecutar el corpus offline.
5. Integrar mediante PR y actualizar los SHA de código de los consumidores privados
   que deban leer el nuevo registro, con una ejecución controlada y revisión del
   snapshot resultante.

Los permisos de publicación siguen siendo una decisión separada. Una fuente nueva
puede requerir un checkpoint, pero el registro no amplía automáticamente la lista
exacta de rutas de `publication_pr.py`, las excepciones de `CODEOWNERS` ni los
archivos transferidos por el publicador. Cualquier ampliación de esos permisos
requiere su propia revisión; hasta entonces el bot rechaza el archivo nuevo.
