# Extracción, evaluación y publicación automática con Gemini

Gemini se ejecuta en GitHub Actions del repositorio **privado**
`RauPro/MDH-SV-review`. La web, la API REST y el MCP no reciben la clave ni llaman
al modelo cuando una persona consulta el monitor. Las fichas de noticias se
publican por campo después de validación automática; la intervención humana se
dedica a solicitudes públicas no sensibles y al [canal editorial privado](PRIVATE-REPORTING.md). El catálogo de casos y las estadísticas
oficiales conservan sus procesos de revisión humana separados.

## Configuración, horario y presupuesto

- Secreto de Actions: `GEMINI_API_KEY`, cargado desde el `.env` local mediante la
  API de secretos cifrados de GitHub. Su valor no se guarda en archivos versionados,
  comandos de terminal ni logs.
- Variable de Actions: `GEMINI_MODEL=gemini-3.5-flash-lite`. La extracción y la
  evaluación emplean el modelo configurado. La segunda pasada no es otro
  proveedor ni constituye una fuente independiente.
- Vercel Cron solicita **Preparar fichas con Gemini** cada hora al minuto 07;
  GitHub conserva el respaldo al minuto 23. Ambos minutos coinciden en UTC y El
  Salvador. La configuración está en [HOURLY-INGESTION.md](HOURLY-INGESTION.md).
  Un HTTP 202 acredita aceptación del disparo, no fichas terminadas ni publicación.
- El extractor recoge RSS e incorpora hasta 50 referencias antiguas a la cola.
  El máximo solicitado no equivale al número procesado: el presupuesto efectivo
  considera las solicitudes disponibles y las horas que faltan hasta su reinicio.
  `runtime/gemini-hourly.json` evita repetir el lote de extracción de una misma hora.
- Clasificación, extracción, evaluación automática, actualizaciones por cambio
  de fuente y reintentos comparten `runtime/gemini-budget.json`. Se reserva saldo
  antes de cada solicitud HTTP. Los recibos de evaluación no abren otra cuota.
- La configuración `operator_cap` conserva un **tope propio de 240 solicitudes
  diarias**, 2 solicitudes por minuto y un límite de entrada de 200 000 unidades
  conservadoras por minuto. No es una cuota confirmada por Google; el límite diario de este
  proyecto no aparece disponible. Un 429 detiene las llamadas hasta el siguiente
  día del Pacífico. No se activa facturación ni se cambia de modelo para continuar.
- `automatic_validation_limit` en el publicador limita las llamadas adicionales
  de cada ejecución, con valor predeterminado 50. Es sólo un techo: el reparto
  horario y el saldo compartido determinan cuántas pueden ejecutarse. Cada llamada
  puede evaluar hasta cuatro fichas. Una ejecución manual no reinicia el contador
  y una solicitud en GitHub tampoco lo elude.

El reparto se calcula con el trabajo elegible de clasificación, extracción y
validación; no usa la cantidad de issues abiertas. Con las tres colas ocupadas
y un cupo horario de 10 llamadas, reserva 2 para clasificación, 6 para extracción
y 2 para evaluar hasta 8 fichas. Cuando termina la clasificación, ese mismo cupo
puede repartir 8 para extracción y 2 para validación. Son ejemplos de asignación,
no cantidades garantizadas de fichas publicadas. Si una etapa no tiene trabajo,
el plan adapta el reparto; el saldo confirmado que no usan clasificación y
extracción también queda disponible para validación.

`runtime/news-allocation.json` guarda el reparto y su consumo. El publicador
reclama una sola vez la asignación de la ejecución de extracción identificada
por `MDH_ALLOCATION_SOURCE_RUN_ID`, dentro del mismo día de cuota. Un plan ausente,
antiguo o ya reclamado no habilita un cupo alternativo. Los disparos sin ejecución
de extracción asociada reciben un cupo proporcional al saldo y las horas restantes,
registrado una sola vez por `GITHUB_RUN_ID`. Este registro coordina las etapas;
`runtime/gemini-budget.json` sigue siendo quien autoriza y cuenta cada intento HTTP.

Los workflows comparten la exclusión mutua `editorial-inbox`. Se usa `queue: max`
para conservar ejecuciones pendientes sin que un nuevo disparo sustituya otro.
Los tiempos reales dependen de GitHub, Vercel, las fuentes y el saldo disponible;
no se promete una fecha de finalización del backlog.

Para detener todas las llamadas nuevas, desactive tanto el extractor como el
publicador con evaluación automática y evite disparos manuales de pertinencia.
Los borradores y el portal permanecen disponibles. Los flags `MDH_ENABLE_AI` y
`MDH_ENABLE_INGESTION` de `.env.example` sólo controlan el colector local anterior.

Las plantillas están en `ops/review-queue/.github/workflows/`. Al instalarlas,
`MDH_PUBLIC_CODE_REF` se sustituye por el SHA exacto del código revisado. Nunca se
usa código de un pull request con los secretos. El token efímero de Actions
persiste datos sólo en el repositorio privado; la escritura pública utiliza la
deploy key ya configurada y una lista explícita de archivos permitidos.

## Qué recibe Gemini y qué comprueba el código

La extracción recibe título, fecha bibliográfica y texto **público** recuperado
del medio, con el límite de entrada del extractor. No recibe comentarios de
GitHub, testimonios privados, credenciales ni datos del navegador. El cuerpo y
las citas de comprobación se mantienen en memoria, sin exportarlos. El tratamiento
del contenido enviado está sujeto a las condiciones de la cuenta y la API.

El código divide la fuente en párrafos identificados. Gemini propone los campos
y sus referencias; el validador contrasta esas referencias, fechas explícitas,
nombres, atribuciones y restricciones de privacidad. El lugar de publicación,
una sede o una comparación no se convierten en lugar del hecho. Se conservan
valores desconocidos en lugar de completar datos mediante suposiciones.

La evaluación automática relee la fuente y exige el mismo hash que la extracción
y la selección de pertinencia. Evalúa cada campo como respaldado, contradicho o
insuficientemente sustentado, con localizadores y señales de privacidad. Los
artículos y las fichas son datos no confiables, nunca instrucciones. El modelo
no dispone de herramientas ni puede publicar directamente.

### Validación por lotes

`automatic_news evaluate --batch-size 4` agrupa hasta cuatro fichas por solicitud
con el mismo modelo y la misma clave. `--limit` conserva su significado de techo
de intentos HTTP; no cuenta artículos ni concede saldo adicional. La plantilla
usa `VALIDATION_BATCH_SIZE=4` y `VALIDATION_LIMIT=50`, sujetos al reparto anterior.

El lote incluye los párrafos completos de cada texto recuperado, sin recortarlos
para hacer caber más noticias. La entrada total, incluidos instrucciones, esquema
y fichas, se limita a `min(90_000, input_tpm - 1_024)` usando el tamaño UTF-8 del
JSON completo más 1 024 unidades de margen. Es una cota conservadora de admisión,
no un conteo exacto de tokens del proveedor. Cuando no cabe otra ficha, se envía
un lote menor; si una ficha sola no cabe, queda pendiente con diagnóstico. Una
fuente ya marcada como recortada no permite afirmar ausencia de información.

Cada artículo tiene un espacio de referencias propio (`n1-p1`, `n2-p1`, etc.).
La respuesta debe devolver exactamente sus identificadores y hashes, sin filas
adicionales ni duplicadas. Una referencia a otro artículo, hash incorrecto, fila
faltante o respuesta incompleta invalida el lote entero antes de proyectar datos.
La recuperación puede dividirlo en lotes menores, siempre dentro del mismo cupo,
plazo de ejecución y límite de intentos. Cada nuevo envío y reintento HTTP consume
saldo. Los controles de privacidad y publicación siguen aplicándose por ficha.

El motor de reglas no añade hechos provenientes de la respuesta del evaluador:
conserva sólo un subconjunto de los valores ya extraídos, con evidencia válida.
Una denuncia conserva su atribución y carácter presunto. Un nombre de institución
citado como fuente no se transforma automáticamente en responsable. La evaluación
comprueba fidelidad al artículo, **no demuestra la verdad del hecho** ni acredita
corroboración independiente.

## Privacidad y campos incompletos

La minimización actúa por campo y también sobre resumen, descripción y título.
Protege identidades sensibles, contactos, domicilios y datos clínicos identificables;
puede conservar descripciones anónimas y ubicaciones administrativas respaldadas.
Una señal genérica de niños o salud no vacía por sí sola todas las casillas.
Los valores previamente protegidos no se restauran durante la evaluación.

Las restricciones deben conservarse en cada proyección pública: un título
protegido no puede reaparecer en la referencia bibliográfica, y un departamento
protegido no puede filtrarse mediante `locations.json`. La conciliación y la
validación de publicación comprueban los snapshots antes de enviarlos a GitHub.

Todas las noticias conservan estructura de ficha aunque falten datos:

| Estado | Interpretación pública |
| --- | --- |
| `reported` | Dato atribuido a la fuente y respaldado por referencias. |
| `pending` | No se pudo determinar con respaldo suficiente; si aún no hay ficha pública, el dato sigue sin publicar. |
| `not_reported` | Dato no mencionado en el texto completo consultado, según evaluación automática. |
| `withheld` | Dato omitido por privacidad, sin conservar su valor público. |
| `not_applicable` | Campo no aplicable al alcance de la noticia, con explicación. |

Una respuesta inválida o una fuente inaccesible no acreditan ausencia de datos.
Un texto recortado no permite declarar que el artículo completo omite un dato.
La ficha puede publicarse parcialmente, y nunca se suma al catálogo de casos ni
al recuento de víctimas por el simple hecho de existir.

## Persistencia, errores y reanudación

El repositorio privado conserva:

- `drafts/<id>.json`: `news_dossier` y, cuando procede, una propuesta opcional
  `record` para el catálogo de casos. Guarda procedencia, hash, párrafos y metadatos
  de extracción, sin el cuerpo completo de la noticia.
- `processing/<id>.json`: estado de extracción, intentos y código de error acotado.
- `automatic-news/<id>.json`: juicio, proyección segura, huella del borrador y la
  fuente, versión de política, generación de solicitud, intentos y uso de tokens
  cuando corresponde a una evaluación individual. En los lotes, `usage` queda
  nulo: no se inventa un reparto de los tokens agregados entre noticias. Estos
  recibos completos no se publican.
- `runtime/news-allocation.json`: asignaciones de llamadas entre etapas y su
  reclamación por ejecución, sin textos de noticias.
- `runtime/news-requests.json`: estado acotado de solicitudes, sin su prosa ni adjuntos.
- Issues de la cola con enlaces y etiquetas de diagnóstico.

Las etiquetas `ai/ready`, `ai/scope-review` y `ai/privacy-review` describen la
extracción privada; no son por sí mismas permisos ni requisitos de aprobación
humana para publicar noticias. La publicación depende del recibo automático y
sus controles vigentes. `ai/error` conserva el fallo sin simular una extracción.

Los intentos y el presupuesto se persisten antes de llamar al proveedor. Las
respuestas incompletas o inválidas no conceden publicación. Los fallos de fuente
y proveedor conservan códigos acotados y reintentos limitados; no se imprimen
artículos ni respuestas privadas. Una fuente cambiada necesita actualizar su
extracción y los vínculos de selección antes de publicar nuevos campos. La
caché evita evaluar de nuevo una combinación ya resuelta de borrador, fuente,
política y generación de solicitud.

Una pausa por cuota conserva el trabajo. Los recibos no eliminan la posibilidad
de una llamada repetida si el proceso se interrumpe después de recibir respuesta
y antes de persistirla; no hay una transacción única entre Google y GitHub.

## Publicación en producción

El publicador privado `publish-locations.yml` mantiene su nombre histórico,
pero ahora realiza el flujo completo:

1. Aplica exclusiones editoriales y retiradas autorizadas, reconstruye selección
   y bibliografía, y comprueba el inventario privado completo para publicar estados.
2. Recoge solicitudes GitHub de forma acotada y ejecuta `automatic_news evaluate`
   con el saldo compartido. Sólo identificadores y versiones controlan la solicitud.
3. Ejecuta `automatic_news export`, que valida nuevamente recibos, identidad,
   hash, campos y prioridades humanas. Genera `data/automated/news_dossiers.json`
   y el snapshot combinado `apps/web/public/archive/dossiers.json`.
4. Comprueba la geografía independientemente, sin llamadas adicionales a Gemini
   en esa etapa; reconcilia todas las proyecciones y sus restricciones de privacidad.
5. Ejecuta validaciones y auditoría, realiza un commit limitado a los archivos
   autorizados y envía el cambio a `main`. Vercel despliega ese commit.

Las fichas automáticas usan `schema_version: 2`, `human_reviewed: false`, metadatos
humanos nulos y `automation.corroboration: single_source`. Las fichas humanas v1
se conservan separadas y prevalecen. `generated` indica una ficha privada generada;
`automatically_published` requiere una publicación v2 válida. El resto de estados
y el contrato se describen en [NEWS-DOSSIERS.md](NEWS-DOSSIERS.md).

Para verificar producción, comprobar el resultado del workflow, su commit y el
despliegue; después, los snapshots y REST/MCP. Una extracción exitosa no prueba
por sí sola que los campos ya estén desplegados.

Los resúmenes distinguen `http_requests`, `evaluated`, `publishable`, `batch_sizes`
y `recovery_splits`, además del cupo `validation_request_grant`. Hay que medir esos
resultados en ejecuciones reales antes de estimar el fin del backlog: cuatro
fichas por llamada es un máximo de transporte, y los errores, fuentes cambiadas
y decisiones de pertinencia pueden reducir la publicación efectiva.

## Solicitudes y correcciones humanas

«Corrección pública no sensible» abre la plantilla `news-correction.yml` con
identificador y versión. No adjunta campos de la ficha ni el artículo. La primera
solicitud pública por noticia puede generar una reevaluación acotada; repeticiones
y retiradas requieren un mantenedor autorizado. El contenido libre de la solicitud
no se ejecuta ni se envía al modelo. Las decisiones persistentes y la procedencia
se conservan en Git. Las correcciones sensibles y retiradas se reciben por el [canal editorial privado](PRIVATE-REPORTING.md), sin exigir un issue al remitente. @RauPro tramita la solicitud y conserva sólo la decisión pública mínima; el mensaje y sus adjuntos no se envían al modelo ni se copian a Git.

El workflow manual **Revisar noticia** y el importador continúan disponibles para
correcciones editoriales solicitadas. La aprobación identifica a la persona,
exige comprobar privacidad y notas sustantivas; guarda una propuesta privada que
se importa mediante commit/PR y CI. `proposal_kind: case` conserva las reglas
humanas del catálogo de casos. Ninguno de esos pasos es requisito de la
publicación automática ordinaria de noticias. Véanse las [instrucciones de
corrección](../ops/review-queue/README.md).

Referencias del proveedor: [salidas estructuradas de Gemini](https://ai.google.dev/gemini-api/docs/structured-output)
y [límites de la API](https://ai.google.dev/gemini-api/docs/rate-limits).
