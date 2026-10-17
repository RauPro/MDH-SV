# MDH-SV · Monitor de Derechos Humanos de El Salvador

Monitor independiente de **reportes de presuntas violaciones de derechos humanos** para consulta pública, investigación y seguimiento institucional. Cada registro identifica su fuente y un localizador dentro del documento. La existencia de una fuente no equivale a corroboración independiente ni a una determinación judicial.

La edición inicial contiene 17 reportes individuales, 2 reportes colectivos y 5 informes de contexto. Es una muestra documental limitada, con revisión humana pendiente. No mide prevalencia, no constituye un censo y no suma personas entre fuentes. Las organizaciones citadas no patrocinan ni validan el proyecto.

## Integridad de la publicación

El prototipo contenía registros sintéticos atribuidos a organizaciones reales. Se excluyeron del conjunto público. Sus datos y el sistema anterior se conservan **solo localmente**, fuera del historial de publicación. El historial antiguo también contenía una credencial incrustada: esta publicación comienza con un historial limpio y no incluye esa credencial. El 16 de octubre de 2026 se registró la confirmación del titular de su identificación exacta y eliminación en Google; véase el [cierre de P0.3 y los controles del historial publicable](docs/SECRET-HISTORY.md).

- Fuente pública canónica: `data/reviewed/publications.json`.
- Validador y exportador: `python -m mdh_pipeline.publication`.
- Las geometrías públicas son nulas; el departamento no representa un lugar preciso.
- El manifest SHA-256 comprueba los archivos exportados, no la veracidad de los hechos.
- La recolección conserva candidatas en una cola privada. Las fichas de noticias se publican automáticamente por campo tras validación de evidencia y privacidad; los casos y las estadísticas oficiales mantienen su revisión humana.
- La API REST y el MCP remoto consultan exclusivamente las fichas y el archivo bibliográfico públicos.

## Desarrollo y pruebas

Se soporta la aplicación desde un checkout completo con Python 3.13 y Node 22
(mínimo 22.12). `.python-version` y `.nvmrc` declaran esas líneas; las revisiones
de parche pueden actualizarse. Las dependencias se instalan desde los locks.
No se distribuye un paquete Python autónomo con sus datasets: la instalación
editable registra los módulos dentro del checkout.

```sh
python3.13 scripts/bootstrap.py
.venv/bin/python scripts/check.py
```

Para configurar un fork, sus contactos y automatizaciones, siga [la guía de preparación e instancias](docs/FORKS.md). Las pruebas y la web local no requieren secretos.

En la instancia original, la API REST está en [`/api/v1/records`](https://mdh-sv.vercel.app/api/v1/records), [`/api/v1/news`](https://mdh-sv.vercel.app/api/v1/news) y [`/api/v1/stats`](https://mdh-sv.vercel.app/api/v1/stats). El MCP remoto usa **https://mdh-sv.vercel.app/mcp**, transporte Streamable HTTP, lectura anónima sin clave. [Contrato OpenAPI](https://mdh-sv.vercel.app/api/v1/openapi.json) y [guía de integración](docs/API.md).

Para desarrollo: `npm ci`, `npm run test:api`, `npm run dev:api`. El módulo Python `.venv/bin/python -m mdh_mcp.server` conserva el transporte local `stdio`.

Los [contratos públicos y sus fixtures](docs/CONTRACTS.md) y el [registro único de fuentes](docs/SOURCES.md) explican cómo extender el monitor sin duplicar políticas de publicación.

## Uso responsable

Contraste cada reporte con su documento original. No use las fichas para determinar culpabilidad, elegibilidad o riesgo de personas. Para errores no sensibles, use los formularios públicos con el identificador del registro. Para correcciones sensibles o retiradas, use el [canal editorial privado de esta instancia](SECURITY.md#correcciones-editoriales-sensibles-y-retiradas); no necesita abrir un issue público. Las vulnerabilidades técnicas tienen un canal privado separado en [Seguridad](SECURITY.md). No adjunte denuncias, nombres de personas protegidas ni documentos privados al primer contacto. El objetivo de primera respuesta privada es de 7 días naturales, orientativo y sin atención de emergencias.

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

La cola se configura en `instance.json` y debe ser privada. En la instancia original es `RauPro/MDH-SV-review`. El procesamiento horario incorpora RSS y referencias históricas, deduplica por URL y conserva decisiones y reintentos. Gemini extrae una ficha y una segunda pasada del mismo proveedor evalúa sus campos contra la misma versión del artículo. Las reglas locales conservan sólo datos respaldados y publicables. La intervención humana atiende solicitudes públicas no sensibles y solicitudes privadas recibidas por el [canal editorial](docs/PRIVATE-REPORTING.md); la publicación de noticias no exige aprobación previa.

Los workflows reproducibles están en [ops/review-queue](ops/review-queue/README.md). El enlace «Corrección pública no sensible» abre una solicitud con identificador y versión, sin adjuntar valores de la ficha. «Corrección sensible o retirada en privado» abre el correo editorial sin copiar contenido de la noticia. Las reevaluaciones usan el mismo presupuesto limitado; retiradas y cambios manuales requieren un mantenedor autorizado. El catálogo de casos conserva el importador de propuestas con aprobación humana y no recibe casos automáticamente de las noticias.

## Continuidad de la investigación WSDS

El monitor amplía la implementación del artículo [Generative AI-based tool for efficient processing of Human Rights violations-related news](https://ieeexplore.ieee.org/document/10933836), DOI `10.1109/CONCAPAN63470.2024.10933836`. Se verificó el resumen y la fecha **Date Added to IEEE Xplore: 26 de marzo de 2025**; el texto completo requiere suscripción. La correspondencia de los campos de investigación se contrastó con el código original conservado localmente.

- **Búsqueda histórica desde 2010:** el periodo solicitado abarca desde el 1 de enero de 2010 hasta la fecha de El Salvador de cada ejecución, con Co Latino, GatoEncerrado, Factum, El Faro, Diario El Mundo, el archivo de El Diario de Hoy y La Prensa Gráfica. Esta última tiene acceso automatizado restringido y no aporta noticias automáticamente; los índices de las demás fuentes pueden ser parciales. No garantiza referencias disponibles ni meses recorridos para todo ese intervalo: las fechas configuradas, cifras y estados por medio y mes se consultan en la cobertura del archivo. La fecha del paper es un antecedente, no el límite inicial. La clasificación territorial es automática y no confirma jurisdicción; las referencias aún requieren lectura y evaluación de pertinencia; los campos se publican después de la validación automática. No se cuentan como violaciones.
- **Fichas estructuradas:** hechos, personas afectadas, lugar, fechas, actores, fuentes, temas, grupos, hipótesis atribuida e instrumentos. Cada valor tiene estado y evidencia; los datos protegidos o no consignados quedan explícitos. [Esquema](docs/DOSSIER-SCHEMA.md).
- **Reutilización:** API REST y MCP remoto de solo lectura con filtros, paginación, atribución y estado editorial, para proyectos externos.

La búsqueda se actualiza cada seis horas. El procesador horario incorpora hasta cincuenta candidatas locales o de ámbito incierto para procesamiento privado, además de las RSS. El archivo público sólo muestra noticias seleccionadas por pertinencia en derechos humanos y vínculo con El Salvador, con evidencia del contenido. Las clasificaciones antiguas requieren reevaluación; una coincidencia de palabras no permite publicar. Publica fichas completas o parciales cuando sus campos superan los controles automáticos, con atribución a una fuente única y sin afirmar revisión humana. [Selección temática](docs/NEWS-SELECTION.md), [colector y cobertura](ops/history/README.md), [borradores privados](docs/PRIVATE-DRAFTS.md).

## Gemini en el flujo privado de producción

El workflow **Preparar fichas con Gemini**, instalado en la cola privada configurada,
está programado para recoger noticias nuevas y procesar el backlog cada hora,
según el presupuesto disponible. El disparo principal de Vercel está configurado
al minuto 07 y GitHub conserva el respaldo al minuto 23; los eventos y recibos de
ejecución deben comprobarse después de cada cambio de configuración. El tope propio configurado
es de 240 solicitudes diarias, compartidas entre clasificación, extracción, evaluación y reintentos; no es una cuota confirmada
de Google, cuyo límite diario sigue sin estar visible. Utiliza el
secreto de Actions `GEMINI_API_KEY` y la variable `GEMINI_MODEL` (Gemini 3.5 Flash Lite).
La clave nunca llega a Vercel, al navegador, al MCP ni a este repositorio público.

La evaluación agrupa hasta cuatro fichas por llamada, con evidencias y controles
de privacidad separados por artículo. El reparto horario se adapta al trabajo
pendiente: con un cupo de 10 llamadas y las tres etapas ocupadas, asigna 2 a
clasificación, 6 a extracción y 2 a evaluar hasta 8 fichas; al terminar la
clasificación puede asignar 8 a extracción y 2 a evaluación. El techo de 50
llamadas por ejecución no concede saldo adicional: cada etapa recibe su parte
del presupuesto compartido, y cada intento se reserva antes del envío.
Los límites de 240 llamadas diarias, 2 por minuto y entrada de 200 000 unidades
conservadoras por minuto se mantienen. Las fuentes largas se agrupan en lotes
menores, sin recortar texto para acelerar; los fallos no autorizan publicación.
El avance efectivo se mide en fichas evaluadas y publicadas, no en issues abiertas
ni en el máximo teórico de artículos por llamada.

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
