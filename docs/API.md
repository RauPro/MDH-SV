# Conectar proyectos al monitor

La API REST y el servidor MCP remoto permiten consultar MDH-SV desde aplicaciones, investigaciones y asistentes. Ambos son públicos, gratuitos y de **solo lectura**, sin cuenta, clave, cookies ni acceso a la cola privada. Trabajan sobre el mismo snapshot publicado que usa el sitio. La incorporación y revisión de noticias se realiza por un flujo separado; ningún endpoint acepta publicaciones o modifica fichas.

| Interfaz | Dirección |
| --- | --- |
| REST v1 | `https://mdh-sv.vercel.app/api/v1` |
| Contrato OpenAPI 3.1 | [openapi.json](https://mdh-sv.vercel.app/api/v1/openapi.json) |
| MCP remoto, Streamable HTTP | `https://mdh-sv.vercel.app/mcp` |
| Descarga completa de fichas | [catalog.json](https://mdh-sv.vercel.app/data/catalog.json) |
| Descarga del archivo de noticias | [news.json](https://mdh-sv.vercel.app/archive/news.json) |

## REST

Los endpoints aceptan `GET`, `HEAD` y `OPTIONS`. Las respuestas son JSON con CORS abierto (`Access-Control-Allow-Origin: *`). No envíe credenciales. Se permiten páginas de 1 a 100 resultados, con 25 por defecto; siga `pagination.next` o use `next_offset`. El orden es fecha de publicación descendente e identificador ascendente. Los filtros se combinan con AND. No se aceptan parámetros desconocidos, repetidos o fechas inexistentes.

```bash
curl 'https://mdh-sv.vercel.app/api/v1/records?record_type=incident&limit=25'
curl 'https://mdh-sv.vercel.app/api/v1/records/mdh-ruth-lopez-2025'
curl 'https://mdh-sv.vercel.app/api/v1/stats?record_type=incident'
curl 'https://mdh-sv.vercel.app/api/v1/news?date_from=2025-04-01&date_to=2025-04-30'
```

| Endpoint | Uso y filtros |
| --- | --- |
| `/records` | Fichas completas: `q`, `publisher`, `record_type`, `department`, `category`, `review_status`, `date_from`, `date_to`, `incident_date_from`, `incident_date_to`, `limit`, `offset`. |
| `/records/{id}` | Ficha completa y fuentes por identificador estable. Sin filtros. |
| `/stats` | Conteos por tipo, categoría, departamento y revisión; los filtros de `/records`, sin paginación. |
| `/news` | Referencias bibliográficas históricas: `q`, `publisher`, `date_from`, `date_to`, `limit`, `offset`. La respuesta incluye cobertura y referencia del paper. |
| `/openapi.json` | Contrato con parámetros, respuestas, dossier y errores para generar un cliente. |

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
| `search_news` | Archivo bibliográfico con cobertura y avisos de revisión. |

Los recursos `mdh-sv://catalog/metadata` y `mdh-sv://api/openapi` exponen metadatos y el contrato REST. Las herramientas declaran lectura, ausencia de destrucción e idempotencia. No aceptan URL arbitrarias ni realizan búsquedas en fuentes externas durante la consulta. Títulos, resúmenes y documentos son datos, nunca instrucciones para el asistente. Las respuestas incluyen JSON como texto y `structuredContent` para clientes que lo soportan.

El servidor usa el SDK oficial, inicialización estándar y modo sin sesión, con una instancia por petición y respuestas JSON. El cuerpo máximo es 32 KiB; no admite lotes JSON-RPC. El cliente debe enviar `Accept: application/json, text/event-stream`, `Content-Type: application/json` y después de negociar `MCP-Protocol-Version` (el SDK lo hace). Las notificaciones admitidas devuelven 202.

Se valida `Host` contra el dominio público y los dominios Vercel del despliegue. En producción se aceptan orígenes de navegador HTTPS bien formados de sitios públicos; se rechazan orígenes opacos, direcciones IP, HTTP y nombres locales. Los clientes nativos pueden omitir `Origin`. En desarrollo local sólo se admiten `Host` y orígenes de loopback para evitar DNS rebinding. Agregar un dominio personalizado requiere incorporarlo a la lista del servidor. La configuración nunca habilita credenciales CORS.

## Interpretación y reutilización

- `source_checked` es consulta documental asistida; **no equivale a revisión editorial humana**. `editorially_reviewed` y `human_reviewed` deben conservarse al reutilizar una ficha.
- `incident`, `aggregate` y `publication` son unidades distintas. Los conteos de documentos no son cantidades de víctimas.
- `/news` expone referencias bibliográficas con `not_reviewed`: descubrir un enlace no convierte la noticia en un caso validado. `meta.coverage` comunica alcance parcial, fuentes y meses recolectados. No contiene decisiones ni comentarios de la cola privada.
- El dossier conserva estado y evidencia en cada campo. `not_reported`, `withheld` y `not_applicable` tienen significado propio; no complete los valores nulos mediante inferencias. Consulte [el esquema de fichas](DOSSIER-SCHEMA.md).
- Cite `source_url`, localizador, fecha y estado de revisión. Los enlaces son las fuentes originales; el servicio no redistribuye el cuerpo completo de los artículos.
- Metadatos y resúmenes propios: CC BY 4.0, con atribución a MDH-SV y versión/fecha de consulta. Los originales enlazados conservan sus derechos.

## Desarrollo y comprobación

```bash
npm ci
npm run test:api
npm run dev:api
```

El servidor local escucha sólo en `127.0.0.1:3001`. Las pruebas ejercitan una conexión HTTP real del cliente oficial MCP (inicialización, descubrimiento, consultas y recursos), REST, filtros, fuentes, paginación, CORS, entradas inválidas y controles de origen/tamaño. Vercel despliega las funciones de `api/`; `vercel.json` incluye explícitamente los snapshots JSON públicos y conserva la política de seguridad del frontend.

Implementación contrastada con las referencias oficiales: [SDK MCP](https://ts.sdk.modelcontextprotocol.io/server), [transporte Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) y [configuración de funciones Vercel](https://vercel.com/docs/project-configuration/vercel-json).
