# MDH-SV · Monitor de Derechos Humanos de El Salvador

Monitor independiente de **reportes de presuntas violaciones de derechos humanos** para consulta pública, investigación y seguimiento institucional. Cada registro identifica su fuente y un localizador dentro del documento. La existencia de una fuente no equivale a corroboración independiente ni a una determinación judicial.

La edición inicial contiene 17 reportes individuales, 2 reportes colectivos y 5 informes de contexto. Es una muestra documental limitada, con revisión humana pendiente. No mide prevalencia, no constituye un censo y no suma personas entre fuentes. Las organizaciones citadas no patrocinan ni validan el proyecto.

## Integridad de la publicación

El prototipo contenía registros sintéticos atribuidos a organizaciones reales. Se excluyeron del conjunto público. Sus datos y el sistema anterior se conservan **solo localmente**, fuera del historial de publicación. El historial antiguo también contenía una credencial incrustada: esta publicación comienza con un historial limpio y no incluye esa credencial. Su titular debe revocarla en Google si aún existe.

- Fuente pública canónica: `data/reviewed/publications.json`.
- Validador y exportador: `python -m mdh_pipeline.publication`.
- Las geometrías públicas son nulas; el departamento no representa un lugar preciso.
- El manifest SHA-256 comprueba los archivos exportados, no la veracidad de los hechos.
- La recolección conserva candidatas en una cola privada. Las fichas de noticias se publican automáticamente por campo tras validación de evidencia y privacidad; los casos y las estadísticas oficiales mantienen su revisión humana.
- La API REST y el MCP remoto consultan exclusivamente las fichas y el archivo bibliográfico públicos.

## Desarrollo y pruebas

Requiere Python 3.12+ y Node 22.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e . --no-deps
.venv/bin/pytest -q
.venv/bin/python -m mdh_pipeline.publication
```

La API REST está en [`/api/v1/records`](https://mdh-sv.vercel.app/api/v1/records), [`/api/v1/news`](https://mdh-sv.vercel.app/api/v1/news) y [`/api/v1/stats`](https://mdh-sv.vercel.app/api/v1/stats). El MCP remoto usa **https://mdh-sv.vercel.app/mcp**, transporte Streamable HTTP, lectura anónima sin clave. [Contrato OpenAPI](https://mdh-sv.vercel.app/api/v1/openapi.json) y [guía de integración](docs/API.md).

Para desarrollo: `npm ci`, `npm run test:api`, `npm run dev:api`. El módulo Python `.venv/bin/python -m mdh_mcp.server` conserva el transporte local `stdio`.

## Uso responsable

Contraste cada reporte con su documento original. No use las fichas para determinar culpabilidad, elegibilidad o riesgo de personas. Para errores no sensibles, abra un issue con el identificador del registro. No adjunte denuncias, nombres de víctimas ni documentos privados: el repositorio es público y no es un canal confidencial ni de emergencia.

Código nuevo bajo MIT; metadatos y resúmenes propios bajo CC BY 4.0. Los documentos originales conservan los derechos y condiciones de sus autores.

## Portal y despliegue

```sh
npm ci
npm run test:api
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run dev
```

Vercel usa la raíz del repositorio y `vercel.json`. La consulta pública no necesita variables de entorno; el disparador horario requiere las credenciales separadas descritas en [ingreso horario](docs/HOURLY-INGESTION.md). El build verifica que todas las descargas correspondan al conjunto canónico. Más detalles en [operación](docs/OPERATIONS.md), [metodología](docs/METHODOLOGY.md) y [seguridad](SECURITY.md).


## Recolección, publicación automática y correcciones

La cola está en el repositorio privado `RauPro/MDH-SV-review`. El procesamiento horario incorpora RSS y referencias históricas, deduplica por URL y conserva decisiones y reintentos. Gemini extrae una ficha y una segunda pasada del mismo proveedor evalúa sus campos contra la misma versión del artículo. Las reglas locales conservan sólo datos respaldados y publicables. La intervención humana se reserva para solicitudes en GitHub; la publicación de noticias no exige aprobación previa.

Los workflows reproducibles están en [ops/review-queue](ops/review-queue/README.md). El botón «Reportar un problema» abre una solicitud pública con identificador y versión, sin adjuntar contenido sensible. Las reevaluaciones usan el mismo presupuesto limitado; retiradas y cambios manuales requieren un mantenedor autorizado. El catálogo de casos conserva el importador de propuestas con aprobación humana y no recibe casos automáticamente de las noticias.

## Continuidad de la investigación WSDS

El monitor amplía la implementación del artículo [Generative AI-based tool for efficient processing of Human Rights violations-related news](https://ieeexplore.ieee.org/document/10933836), DOI `10.1109/CONCAPAN63470.2024.10933836`. Se verificó el resumen y la fecha **Date Added to IEEE Xplore: 26 de marzo de 2025**; el texto completo requiere suscripción. La correspondencia de los campos de investigación se contrastó con el código original conservado localmente.

- **Búsqueda histórica desde 2010:** el periodo solicitado abarca desde el 1 de enero de 2010 hasta la fecha de El Salvador de cada ejecución, con Co Latino, GatoEncerrado, Factum, El Faro, Diario El Mundo, el archivo de El Diario de Hoy y La Prensa Gráfica. Esta última tiene acceso automatizado restringido y no aporta noticias automáticamente; los índices de las demás fuentes pueden ser parciales. No garantiza referencias disponibles ni meses recorridos para todo ese intervalo: las fechas configuradas, cifras y estados por medio y mes se consultan en la cobertura del archivo. La fecha del paper es un antecedente, no el límite inicial. La clasificación territorial es automática y no confirma jurisdicción; las referencias aún requieren lectura y evaluación de pertinencia; los campos se publican después de la validación automática. No se cuentan como violaciones.
- **Fichas estructuradas:** hechos, personas afectadas, lugar, fechas, actores, fuentes, temas, grupos, hipótesis atribuida e instrumentos. Cada valor tiene estado y evidencia; los datos protegidos o no consignados quedan explícitos. [Esquema](docs/DOSSIER-SCHEMA.md).
- **Reutilización:** API REST y MCP remoto de solo lectura con filtros, paginación, atribución y estado editorial, para proyectos externos.

La búsqueda se actualiza cada seis horas. El procesador horario incorpora hasta cincuenta candidatas locales o de ámbito incierto para procesamiento privado, además de las RSS. El archivo público sólo muestra noticias seleccionadas por pertinencia en derechos humanos y vínculo con El Salvador, con evidencia del contenido. Las clasificaciones antiguas requieren reevaluación; una coincidencia de palabras no permite publicar. Publica fichas completas o parciales cuando sus campos superan los controles automáticos, con atribución a una fuente única y sin afirmar revisión humana. [Selección temática](docs/NEWS-SELECTION.md), [colector y cobertura](ops/history/README.md), [borradores privados](docs/PRIVATE-DRAFTS.md).

## Gemini en el flujo privado de producción

El workflow **Preparar fichas con Gemini**, instalado en `RauPro/MDH-SV-review`,
está programado para recoger noticias nuevas y procesar el backlog cada hora,
según el presupuesto disponible. El disparo principal de Vercel está configurado
al minuto 07 y GitHub conserva el respaldo al minuto 23; los eventos y recibos de
ejecución deben comprobarse después de cada cambio de configuración. El tope propio configurado
es de 240 solicitudes diarias, compartidas entre clasificación, extracción, evaluación y reintentos; no es una cuota confirmada
de Google, cuyo límite diario sigue sin estar visible. Utiliza el
secreto de Actions `GEMINI_API_KEY` y la variable `GEMINI_MODEL` (Gemini 3.5 Flash Lite).
La clave nunca llega a Vercel, al navegador, al MCP ni a este repositorio público.

Los borradores se guardan en `drafts/` y las evaluaciones en `automatic-news/`,
dentro del repositorio privado. El publicador prepara fichas v2 con
`human_reviewed: false` y `automation.corroboration: single_source`: una segunda
pasada de Gemini no es una fuente ni un proveedor independiente. Los campos
dudosos quedan sin resolver y los sensibles se omiten; no se inventan datos.
Los errores de acceso o cuota quedan en cola con reintentos acotados. El
publicador conserva por separado los datos automáticos y las decisiones humanas,
que prevalecen. [Contrato y publicación](docs/NEWS-DOSSIERS.md).

[Funcionamiento, cuotas, reanudación y revisión](docs/GEMINI-REVIEW.md).

Cada noticia del archivo tiene una ficha estructurada, aunque esté pendiente o
incompleta. Los datos no mencionados, los no evaluados y los protegidos se
distinguen; una ficha de noticia no se cuenta automáticamente como violación.
[Flujo y estados de las fichas](docs/NEWS-DOSSIERS.md).
