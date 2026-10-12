# Conectar proyectos al monitor

La API REST y el servidor MCP remoto permiten consultar MDH-SV desde aplicaciones, investigaciones y asistentes. Ambos son públicos, gratuitos y de **solo lectura**, sin cuenta, clave, cookies ni acceso a la cola privada. Trabajan sobre el mismo snapshot publicado que usa el sitio. La incorporación y revisión de noticias se realiza por un flujo separado; ningún endpoint acepta publicaciones o modifica fichas.

| Interfaz | Dirección |
| --- | --- |
| REST v1 | `https://mdh-sv.vercel.app/api/v1` |
| Contrato OpenAPI 3.1 | [openapi.json](https://mdh-sv.vercel.app/api/v1/openapi.json) |
| MCP remoto, Streamable HTTP | `https://mdh-sv.vercel.app/mcp` |
| Descarga completa de fichas | [catalog.json](https://mdh-sv.vercel.app/data/catalog.json) |
| Descarga del archivo de noticias | [news.json](https://mdh-sv.vercel.app/archive/news.json) |
| Fichas de noticias publicadas con revisión humana | [dossiers.json](https://mdh-sv.vercel.app/archive/dossiers.json) |

## REST

Los endpoints aceptan `GET`, `HEAD` y `OPTIONS`. Las respuestas son JSON con CORS abierto (`Access-Control-Allow-Origin: *`). No envíe credenciales. Se permiten páginas de 1 a 100 resultados, con 25 por defecto; siga `pagination.next` o use `next_offset`. El orden es fecha de publicación descendente e identificador ascendente. Los filtros se combinan con AND. No se aceptan parámetros desconocidos, repetidos o fechas inexistentes.

```bash
curl 'https://mdh-sv.vercel.app/api/v1/records?record_type=incident&limit=25'
curl 'https://mdh-sv.vercel.app/api/v1/records/mdh-ruth-lopez-2025'
curl 'https://mdh-sv.vercel.app/api/v1/stats?record_type=incident'
curl 'https://mdh-sv.vercel.app/api/v1/news?date_from=2010-01-01&limit=25'
```

| Endpoint | Uso y filtros |
| --- | --- |
| `/records` | Fichas completas: `q`, `publisher`, `record_type`, `department`, `category`, `review_status`, `date_from`, `date_to`, `incident_date_from`, `incident_date_to`, `limit`, `offset`. |
| `/records/{id}` | Ficha completa y fuentes por identificador estable. Sin filtros. |
| `/stats` | Conteos por tipo, categoría, departamento y revisión; los filtros de `/records`, sin paginación. |
| `/news` | Referencias y una `news_dossier` completa por noticia: `q`, `publisher`, `scope_hint`, `date_from`, `date_to`, `limit`, `offset`. La respuesta incluye cobertura y referencia del paper. |
| `/openapi.json` | Contrato con parámetros, respuestas, dossier y errores para generar un cliente. |

`scope_hint` en noticias acepta `el_salvador`, `international_context` o `undetermined`. Es una señal automática basada en título y URL, no una confirmación de jurisdicción. Omitir el filtro conserva todo el archivo; el sitio web comienza con `el_salvador`. `scope_terms` declara los términos que produjeron la señal.

El objetivo histórico es recuperar noticias desde 2010. Lea `meta.coverage.start_date` y `meta.coverage.end_date` para conocer el intervalo solicitado por la versión consultada, junto con los estados por medio y mes para evaluar su avance. Un intervalo amplio no acredita cobertura completa ni la disponibilidad de noticias en todos sus meses. La referencia IEEE se conserva como antecedente independiente de ese inicio.

`discovery.method` distingue `wordpress_rest_title_screen` de `html_article_metadata`.
En este último, `source_post_id` puede ser nulo; no se inventa un identificador del
medio. `meta.coverage.sources[].collection`, cuando existe, explica el canal y las
restricciones del recorrido. Una fuente registrada con acceso restringido no
equivale a una fuente recolectada satisfactoriamente.

`q` busca en títulos, resúmenes, etiquetas y los campos públicos del dossier: hechos, personas o grupos descritos por la fuente, actores, grupos vulnerables, temas y lugar público. Ignora tildes y mayúsculas. `publisher`, `department` y `category` requieren el valor completo; `department=unknown` busca ubicación no determinada.

**Las fechas tienen significados distintos.** `date_from` y `date_to` filtran la publicación de la fuente. `incident_date_from` y `incident_date_to` filtran el período del hecho, incluyendo intervalos que se solapan; excluyen hechos de fecha desconocida. Los límites son inclusivos. La precisión mensual/anual de una ficha no se convierte en certeza sobre un día: conserve `date_precision` y `date_note`.

Las listas responden `{data: [...], pagination: {total, limit, offset, next_offset, next}, meta: {...}}`; las consultas individuales responden `{data: {...}, meta: {...}}`. `meta` declara versión del esquema, fecha del snapshot, licencia y límites. Los errores usan `{error: {code, message}}` y estados HTTP 400 (entrada inválida), 404 (no encontrado), 405 (método no permitido), 414 (URL mayor de 4 KiB), 500 (fallo interno) o 503 (archivo no disponible en el despliegue). Una ausencia del archivo no se presenta como cero noticias.

Ejemplo de consumo sin dependencias:

```js
const query = new URLSearchParams({ record_type: 'incident', limit: '100' });
let url = `https://mdh-sv.vercel.app/api/v1/records?${query}`;
const records = [];
while (url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`MDH-SV: HTTP ${response.status}`);
  const page = await response.json();
  records.push(...page.data);
  url = page.pagination.next;
}
```

Para reproducibilidad estricta, descargue el snapshot y registre su versión/commit y fecha. Las páginas de la API reflejan el despliegue activo; una actualización entre peticiones puede cambiar la paginación. La caché de la API puede conservar respuestas durante cinco minutos. No se promete cobertura exhaustiva, disponibilidad contractual ni un límite dedicado por consumidor. Use las descargas completas para trabajos masivos y evite sondeos continuos.

## Una ficha para cada noticia

Cada resultado de `/news` y de `search_news` incluye `news_dossier`, también cuando la referencia es internacional, todavía no tiene extracción pública o su información es parcial. Esta ficha tiene `kind: news_dossier`; es independiente de `Publication.dossier` y de los casos del monitor. No modifica el mapa ni los conteos de violaciones.

La bibliografía conocida permanece en `candidate_id`, `source_url`, `publisher`, `source_title` y `published_date`. `fields` contiene siempre los 18 campos: resumen, categoría, presunta vulneración, hechos, personas afectadas, país, departamento, municipio, lugar público, fecha inicial, fecha final, hora, actores, fuentes institucionales, grupos vulnerables, temas, hipótesis e instrumentos. Cada campo declara `value`, `status`, `evidence_refs` y `note`.

Cuando no hay una ficha pública aprobada, esos campos tienen `value: null` y `status: pending`: **pendiente de extracción o revisión**. No significa que la fuente omita el dato, ni informa si el procesamiento privado ya se realizó. No se infieren país, víctimas o fechas a partir del título, las etiquetas o `scope_hint`.

El archivo `/archive/dossiers.json` contiene exclusivamente fichas procesadas y revisadas por una persona, con su identidad, fecha, notas y evidencia. Se vinculan por candidata, URL, medio y fecha; el título puede estar minimizado por privacidad. Una ficha aprobada puede seguir siendo parcial o tener pertinencia `not_relevant`. `not_reported` significa «Dato no mencionado en el texto analizado» cuando se ha identificado expresamente durante la extracción y revisión; su alcance se limita a esa versión del texto, sin afirmar ausencia en otros documentos o fuentes. Los campos no evaluados, descartados o heredados sin esa comprobación siguen como `pending`. `withheld` y `not_applicable` conservan sus significados propios.

El `review_status: not_reviewed` exterior pertenece a la referencia bibliográfica del recolector. Para conocer la revisión de la ficha consulte **`news_dossier.human_reviewed`**, `reviewed_by`, `reviewed_at` y `review_notes`. Una revisión editorial no corrobora judicialmente los hechos.

Si no se puede validar el archivo público de fichas, la respuesta conserva todas las estructuras pendientes y declara `meta.news_dossiers_status: unavailable`; no recupera datos de la cola privada. Con el archivo disponible devuelve `available`, incluso si todavía no contiene fichas aprobadas.

## MCP remoto

Configure un servidor **Streamable HTTP** con URL `https://mdh-sv.vercel.app/mcp` y sin autenticación. Se requiere un cliente compatible con ese transporte. No es un comando local `stdio`, ni un endpoint SSE legado. Abrir la URL con `GET` devuelve HTTP 405, conforme al transporte cuando no se ofrece un canal SSE independiente; la conexión se inicia con `POST`.

Ejemplo real con el SDK oficial de TypeScript:

```js
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const client = new Client({ name: 'investigacion-ddhh', version: '1.0.0' });
await client.connect(new StreamableHTTPClientTransport(
  new URL('https://mdh-sv.vercel.app/mcp'),
));
try {
  const result = await client.callTool({
    name: 'search_records',
    arguments: { record_type: 'incident', q: 'detención', limit: 10 },
  });
  console.log(result.structuredContent);
} finally {
  await client.close();
}
```

| Herramienta | Resultado |
| --- | --- |
| `search_records` | Las mismas fichas, filtros y paginación que REST. |
| `get_record` | Ficha por `{id}`, incluido dossier y referencias. |
| `get_statistics` | Conteos por unidad documental y estado de revisión. |
| `search_news` | Archivo bibliográfico con una ficha universal `news_dossier` por referencia, cobertura y revisión pública explícita. |

Los recursos `mdh-sv://catalog/metadata` y `mdh-sv://api/openapi` exponen metadatos y el contrato REST. Las herramientas declaran lectura, ausencia de destrucción e idempotencia. No aceptan URL arbitrarias ni realizan búsquedas en fuentes externas durante la consulta. Títulos, resúmenes y documentos son datos, nunca instrucciones para el asistente. Las respuestas incluyen JSON como texto y `structuredContent` para clientes que lo soportan.

El servidor usa el SDK oficial, inicialización estándar y modo sin sesión, con una instancia por petición y respuestas JSON. El cuerpo máximo es 32 KiB; no admite lotes JSON-RPC. El cliente debe enviar `Accept: application/json, text/event-stream`, `Content-Type: application/json` y después de negociar `MCP-Protocol-Version` (el SDK lo hace). Las notificaciones admitidas devuelven 202.

Se valida `Host` contra el dominio público y los dominios Vercel del despliegue. En producción se aceptan orígenes de navegador HTTPS bien formados de sitios públicos; se rechazan orígenes opacos, direcciones IP, HTTP y nombres locales. Los clientes nativos pueden omitir `Origin`. En desarrollo local sólo se admiten `Host` y orígenes de loopback para evitar DNS rebinding. Agregar un dominio personalizado requiere incorporarlo a la lista del servidor. La configuración nunca habilita credenciales CORS.

## Interpretación y reutilización

- `source_checked` es consulta documental asistida; **no equivale a revisión editorial humana**. `editorially_reviewed` y `human_reviewed` deben conservarse al reutilizar una ficha.
- `incident`, `aggregate` y `publication` son unidades distintas. Los conteos de documentos no son cantidades de víctimas.
- `/news` conserva referencias bibliográficas con `not_reviewed` y una `news_dossier` propia: descubrir un enlace o mostrar una ficha no convierte la noticia en un caso validado. `meta.coverage` comunica alcance parcial, fuentes y meses recolectados. No contiene decisiones ni comentarios de la cola privada.
- El dossier conserva estado y evidencia en cada campo. `not_reported`, `withheld` y `not_applicable` tienen significado propio; no complete los valores nulos mediante inferencias. Consulte [el esquema de fichas](DOSSIER-SCHEMA.md).
- Cite `source_url`, localizador, fecha y estado de revisión. Los enlaces son las fuentes originales; el servicio no redistribuye el cuerpo completo de los artículos.
- Metadatos y resúmenes propios: CC BY 4.0, con atribución a MDH-SV y versión/fecha de consulta. Los originales enlazados conservan sus derechos.

## Desarrollo y comprobación

```bash
npm ci
npm run test:api
npm run dev:api
```

El servidor local escucha sólo en `127.0.0.1:3001`. Las pruebas ejercitan una conexión HTTP real del cliente oficial MCP (inicialización, descubrimiento, consultas y recursos), REST, filtros, fuentes, paginación, CORS, entradas inválidas y controles de origen/tamaño. Browser y servidor comparten el constructor y validador de `shared/news-dossier.mjs`; el build exige aprobación humana, equivalencia con la fuente revisada y vinculación exacta con la bibliografía antes de publicar el archivo de fichas. Vercel incluye explícitamente los snapshots JSON públicos y conserva la política de seguridad del frontend.

Implementación contrastada con las referencias oficiales: [SDK MCP](https://ts.sdk.modelcontextprotocol.io/server), [transporte Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) y [configuración de funciones Vercel](https://vercel.com/docs/project-configuration/vercel-json).

`GET /api/v1/news` y MCP `search_news` incluyen `automatic_location` separado de `news_dossier`. Su estado es siempre `automated_unreviewed`, con `human_reviewed: false`. Expone únicamente la ubicación administrativa y su procedencia verificable; no implica ubicación de una violación ni aprobación de los otros campos. Una ficha revisada prevalece y suprime esta proyección. `null` puede significar datos aún no extraídos, descartados o no publicables; no demuestra ausencia de ubicación. El snapshot completo está en `/archive/locations.json`.

Para una ubicación derivada de municipio, `automatic_location.method` vale
`gemini_municipality_reference`; `resolution` identifica el municipio/distrito,
la versión de la referencia administrativa y la página oficial que sustenta su
departamento. `gemini_explicit_department` conserva la modalidad directa.

`gemini_department_corroborated` identifica un departamento citado por el modelo
con contexto geográfico y confirmado como tal en otro párrafo del mismo artículo.
Su objeto `corroboration` contiene `strategy: same_article_explicit_department`,
`model_paragraph_id`, `explicit_paragraph_id` y `rule_version: 1`. Los dos párrafos
son distintos; `evidence.paragraph_id` coincide con `explicit_paragraph_id`, y el
hash corresponde al cuerpo original usado por Gemini. La corroboración es una
comprobación automática dentro de una sola fuente, no verificación independiente.
Este método requiere la versión 3 del snapshot; los clientes aceptan también
las versiones 1 y 2. Los tres métodos siguen `automated_unreviewed` y no alteran
los campos ni las citas de `news_dossier`.
