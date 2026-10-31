# Contratos públicos y evidencia compartida

Los contratos separan catálogo documental, referencias de prensa, fichas de
noticias, estados de procesamiento y ubicaciones automáticas. Ningún estado de
procesamiento o ubicación sustituye a la revisión necesaria para publicar campos.
Los borradores privados y sus envelopes no son formatos públicos compatibles.

## Implementaciones y ejemplos comunes

| Contrato | Productor o verificación Python | Consumo JavaScript |
| --- | --- | --- |
| Catálogo y dossier documental | `mdh_pipeline.publication.Catalog` | `shared/catalog.mjs`: web, API y prebuild |
| Referencia bibliográfica | `mdh_pipeline.history.validate_reference` | `shared/news-archive.mjs` |
| Selección de noticias | `mdh_pipeline.news_selection.validate_selection` | `shared/news-selection.mjs` |
| Ficha de noticia humana v1 o automática v2 | `mdh_pipeline.news_dossier.validate_published_news_dossier` | `shared/news-dossier.mjs` |
| Estado mínimo de procesamiento | `mdh_pipeline.news_processing.validate_collection` | `shared/news-processing.mjs` |
| Ubicaciones automáticas v1–v5 | `mdh_pipeline.automatic_locations.validate_collection` | `shared/news-location.mjs` |
| Registro de fuentes | `mdh_pipeline.publishers` | `shared/source-registry.mjs` |

Los ejemplos de catálogo, referencias, selección, fichas, procesamiento y
ubicaciones de `tests/fixtures/contracts` se ejecutan en ambos lenguajes con el
mismo resultado esperado. Las fixtures del archivo completo comprueban su
envoltura JavaScript y el rechazo en la API; Python valida las referencias al
construir ese archivo, sin un segundo parser de su envoltura. El registro tiene
además su corpus de configuración en
`tests/fixtures/source-registry-contract.json`. Son datos ficticios: no se llaman
servicios de IA, no se descargan artículos y no se utilizan credenciales.

Las reglas semánticas siguen en los validadores: referencias de evidencia
existentes, fechas coherentes, ausencia de datos en campos protegidos, revisión
humana explícita y procedencia de las proyecciones automáticas. JSON Schema sirve
para describir y comprobar el intercambio; no demuestra por sí solo que una
noticia sea pertinente, una cita sea correcta o una publicación esté autorizada.

El archivo público valida además sus referencias, inventario, cobertura, política
y metadatos del documento de origen. Conserva los campos de telemetría aprobados
y rechaza propiedades desconocidas antes de exponerlos por REST o MCP. Una
referencia candidata puede no tener selección; el archivo público sí exige su
evidencia de selección.

El catálogo JavaScript aplica el mismo contrato en navegador, servidor y
comprobación de exports. El servidor rechaza un catálogo inválido con un error
503 genérico; no intenta convertir candidatos en registros revisados ni expone
sus campos desconocidos. Los defaults documentados se normalizan como en Python;
no se convierten cadenas o números en booleanos de revisión.

## Identidad y privacidad

El identificador de una noticia es SHA-256 de su URL exacta en UTF-8. Los
validadores comprueban esa relación además del formato hexadecimal. La URL y los
campos de procedencia deben coincidir con la referencia bibliográfica actual;
un recibo antiguo no acredita el estado de otra noticia o versión del artículo.
Esto es una vinculación de datos, no una firma, aprobación o prueba del contenido.

JavaScript utiliza `@noble/hashes` con versión exacta y lock para mantener la
comprobación síncrona también en el navegador; las pruebas la contrastan con
`node:crypto`. Python usa `hashlib`. Los workflows que ejecutan los módulos
compartidos instalan el lock de **su checkout de código aprobado** mediante
`npm ci --omit=dev --ignore-scripts`. En el publicador privado se instala desde
`monitor`, antes del checkout con credencial SSH; jamás desde el árbol de datos.

El registro y sus capacidades están documentados en [SOURCES.md](SOURCES.md).
Añadir un host no amplía las rutas que los bots pueden escribir, no concede
aprobación humana y no cambia una fuente restringida a pública.

## Respuestas REST y MCP

`server/openapi.mjs` publica OpenAPI 3.1. La suite
`tests/openapi-responses.test.mjs` abre el servidor local y valida respuestas
HTTP reales usando AJV 2020-12 y `ajv-formats`, fijados en el lock. Comprueba todas
las páginas actuales, fichas individuales, filtros, estadísticas, errores y los
métodos HEAD/OPTIONS sin cuerpo. No modifica las respuestas para hacerlas pasar:
la coerción, la inserción de defaults y la eliminación de propiedades están
desactivadas. La prueba HTTP completa tiene un límite de 60 segundos por archivo
(`npm run test:api-contract`), porque recorre todas las páginas y filtros; el resto
de pruebas API conserva su límite de 15 segundos. `npm run test:api` ejecuta ambas.

La misma suite valida los resultados estructurados de las herramientas MCP con
los esquemas REST correspondientes. Los errores del protocolo se contrastan con
el SDK fijado: conservan el ID legible de una solicitud, y lo omiten cuando no
pudo leerse, conforme a la [especificación MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic#responses).
Los tests negativos detectan campos privados, propiedades desconocidas, campos
obligatorios ausentes, fechas/URIs incorrectas y desviaciones del esquema.

Las estadísticas oficiales conservan su validador compartido de JavaScript y su
verificación desde el proceso de revisión privado; no se inventa un segundo
validador Python para afirmar independencia. Sus respuestas y sus fuentes
permitidas se comprueban también mediante OpenAPI y el registro único.

## Añadir una regla o un ejemplo

1. Identificar el contrato y la versión afectados. Mantener explícita la
   diferencia entre datos públicos y privados y entre una estructura válida y
   su vínculo con el snapshot actual.
2. Añadir al archivo correspondiente una base ficticia o una operación sobre
   una base existente. Cada caso tiene `name`, `base`, `valid` y `operations`.
   Las únicas operaciones son `add`, `replace` y `remove`; `path` es una lista
   de claves o índices. `replace` y `remove` exigen que el destino exista, para
   que un error en la fixture no aparente cubrir una regla.
3. Reproducir el fallo en ambos consumidores y corregir el límite de validación.
   No cambiar una expectativa a válida para conservar una entrada insegura.
   Si una prueba de vínculo usa otra URL, debe darle un SHA válido para esa URL;
   una identidad falsa se rechaza antes de comprobar si la referencia está vieja.
4. Para un cambio público de forma, actualizar OpenAPI y sus ejemplos válidos e
   inválidos. Conservar las versiones admitidas o diseñar su migración explícita;
   no relajar `additionalProperties` para ocultar una incompatibilidad.
5. Ejecutar `.venv/bin/python scripts/check.py` y revisar el diff. Las regeneraciones
   de prueba se hacen fuera del checkout y deben conservar los datos publicados.

Comprobaciones dirigidas:

```sh
.venv/bin/python -m pytest tests/test_catalogue_contract.py tests/test_public_contracts.py tests/test_source_registry.py -q
npm run test:api
npm --prefix apps/web test
```

Los contratos son evidencia ejecutable y acotada, no una afirmación de equivalencia
formal entre lenguajes para toda entrada posible. Una divergencia nueva debe
incorporarse al corpus para impedir que vuelva a pasar inadvertida.

## Utilidades del pipeline

`mdh_pipeline.source_urls` contiene normalización de texto e identidad de URL,
sin importar la cola de GitHub ni realizar consultas DNS. La comprobación DNS
vive en `network_safety`, usada por los lectores de fuentes. No debe llamarse
para validar documentos ya disponibles en memoria.

`file_outputs.write_json_outputs` comparte la escritura recuperable de documentos
validados entre importación y publicación automática. Conserva el contrato de
reemplazo por archivo, rollback de mejor esfuerzo y reintento de P1.3. Los nombres
anteriores en los adaptadores se mantienen como aliases de compatibilidad.
