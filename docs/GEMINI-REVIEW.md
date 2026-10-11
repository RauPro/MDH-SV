# Extracción con Gemini antes de la revisión humana

Gemini se ejecuta en GitHub Actions del repositorio **privado**
`RauPro/MDH-SV-review`. La web, la API REST y el MCP no reciben la clave ni llaman
al modelo cuando una persona consulta el monitor.

## Configuración y programación

- Secreto de Actions: `GEMINI_API_KEY`, cargado desde el `.env` local mediante la
  API de secretos cifrados de GitHub. Su valor no se guarda en archivos versionados,
  comandos de terminal ni logs.
- Variable de Actions: `GEMINI_MODEL=gemini-3.5-flash-lite`. La prueba de integración
  del 10/10/2026 mostró que Google rechazaba `gemini-2.5-flash` para esta cuenta
  aunque apareciera en el listado. Se seleccionó Flash Lite después de comprobar su disponibilidad y menor latencia para la extracción por lotes.
- Workflow: **Preparar fichas con Gemini**, programado cada hora al minuto 23 UTC
  (también al minuto 23 de cada hora en El Salvador). GitHub puede retrasar un job.
- Cada ejecución recoge RSS, incorpora hasta 25 referencias antiguas a la cola
  y procesa hasta 20 noticias con Gemini. La ejecución manual admite de 1 a 50.
- Se reservan los primeros cinco intentos disponibles para noticias RSS nuevas;
  después se procesa el backlog priorizando indicios de El Salvador. Las
  todos los ámbitos territoriales entran en el ingreso histórico ordinario, con
  turnos para referencias salvadoreñas, indeterminadas e internacionales. No se promete una fecha de finalización: depende de cuotas, fuentes,
  revisiones pendientes y disponibilidad de los servicios.

Para detener nuevas llamadas al modelo, desactive **Preparar fichas con Gemini** en Actions del repositorio privado. Los borradores ya guardados y el portal permanecen disponibles. Los flags `MDH_ENABLE_AI` y `MDH_ENABLE_INGESTION` de `.env.example` sólo controlan el colector local anterior.

La plantilla está en `ops/review-queue/.github/workflows/extract.yml`. Al instalarla
en el repositorio privado, `MDH_PUBLIC_CODE_REF` se sustituye por el SHA exacto del
código revisado. Nunca se usa código de un pull request con el secreto. El token
efímero de Actions sólo puede escribir en la cola privada. Los colectores y el
revisor comparten una exclusión mutua para evitar duplicados y escrituras cruzadas. Se usa `queue: max` para conservar hasta cien ejecuciones pendientes y evitar que una captura programada desplace una aprobación humana en espera.

## Qué recibe Google y qué se conserva

Se envían el título, la fecha bibliográfica y el texto **público** recuperado del
medio. El máximo es 24.000 caracteres por noticia. No se adjuntan archivos privados,
comentarios de GitHub, credenciales ni datos del navegador. El cuerpo completo y
las citas de comprobación sólo se mantienen en memoria durante la ejecución.
El tratamiento del contenido enviado a Google está sujeto a las condiciones de
la cuenta y de su API.

Se solicita un esquema JSON compacto compatible con la API de Google. El cliente reconstruye y valida localmente el contrato completo con Pydantic, incluidas las restricciones que la API no admite. El código divide el texto en fragmentos literales identificados y Gemini selecciona esas referencias para cada campo, sin transcribir citas. Las referencias se comprueban contra el texto antes de aceptar el campo. Las fechas sin componentes explícitos (incluido el año) quedan desconocidas. Los nombres de lugar,
instituciones, actores e instrumentos también se contrastan con sus párrafos.
Un país o municipio no se convierte en departamento. Las fechas posteriores a la
publicación y los intervalos invertidos se descartan sin inventar límites. Los
informes generales conservan fecha y ubicación del hecho como «no aplicable»;
los campos descartados quedan señalados para el editor.
Esto detecta referencias inventadas; **no demuestra la verdad del hecho ni que la
cita respalde correctamente una interpretación**. El editor debe revisar ambas
cosas. Los artículos se tratan como datos, aunque contengan instrucciones para un
modelo. Gemini no dispone de herramientas ni de acceso a otros documentos.

Se conservan en el repositorio privado:

- `drafts/<id>.json`: siempre contiene `news_dossier`, con los campos disponibles,
  pendientes, no mencionados o protegidos. Puede conservar además `record` como
  propuesta opcional de caso; modelo, versión del prompt, fecha, hash del texto de entrada,
  párrafos de referencia, hashes de citas, commit del procesador y ejecución de Actions. Incluye el uso de tokens cuando Google
  lo proporciona. No contiene el cuerpo periodístico completo.
- `processing/<id>.json`: estado, número de intentos y código de error sanitizado.
- La issue original: enlace al borrador y una etiqueta de resultado.

Todas las noticias tienen estructura de ficha, aunque su pertinencia sea incierta
o falten datos. Sólo una ausencia señalada expresamente por la extracción se
muestra como «Dato no mencionado en el texto analizado»; los campos no evaluados
o descartados quedan pendientes. Los datos protegidos llevan `withheld`, sin
conservar su valor. La clasificación y la privacidad determinan qué revisar, no
si existe una ficha. El modelo no aprueba ni publica noticias o casos.

## Estados, errores y reanudación

| Etiqueta | Acción editorial |
| --- | --- |
| `ai/ready` | Contrastar y corregir la ficha estructurada. Sigue sin aprobar. |
| `ai/scope-review` | Determinar pertinencia y jurisdicción; existe una ficha de noticia, no un caso validado. |
| `ai/privacy-review` | Revisar la protección de personas en la ficha con campos protegidos. |
| `ai/error` | Revisar código de error y fuente. La estructura pendiente no inventa datos ni afirma una extracción terminada. |

Las respuestas tienen un máximo de 4.096 tokens y 192 KiB. El cliente limita
reintentos de red, 429 y 5xx a tres; el lote se detiene ante cuota, autenticación o configuración
inválida del proveedor. Los fallos ordinarios se reintentan con espera y un máximo de tres
intentos por candidata. `retry_failed` en la ejecución manual permite intentar
otra vez tras corregir la causa. No regenera fichas ya procesadas con el contrato actual. Los borradores anteriores
se migran sin una llamada al modelo cuando contienen datos reutilizables; los
resultados que no extrajeron campos reciben un nuevo presupuesto acotado. El
historial Git conserva las versiones anteriores y las decisiones humanas se respetan.

El índice privado y los estados evitan repetir llamadas al modelo al reanudar.
Si el job se interrumpe después de guardar una ficha, la siguiente ejecución puede
reparar el enlace de la issue sin otra llamada. Los casos aprobados, rechazados o
cerrados se respetan. Un fallo entre la respuesta de Google y su persistencia
puede requerir repetir esa llamada; no hay garantía transaccional entre servicios.

Los fallos de fuente, validación o proveedor se informan con códigos y contadores,
sin volcar textos ni errores de validación que pudieran contenerlos. El resumen de
Actions siempre incluye `publications: 0`.

## Revisión y publicación

El envoltorio privado mantiene `human_reviewed:false` y `publishable:false`. La
ficha de noticia conserva `candidate_id`; una propuesta de caso conserva también
`origin_candidate_id`. La exportación de contenido extraído exige revisión humana.
La estructura pública inicial sólo usa metadatos bibliográficos y campos pendientes. Un editor abre
la fuente, revisa los localizadores y modifica la ficha si corresponde. Luego usa
**Revisar noticia**, confirma la comprobación de privacidad y redacta sus notas.
Elige `proposal_kind: news_dossier` para una ficha de noticia, incluso parcial o
internacional. Puede dejar `record_json` vacío para usar el archivo privado o
proporcionar una ficha corregida. Debe haber leído la fuente y resuelto qué datos
pueden compartirse. `proposal_kind: case` conserva los requisitos adicionales del
catálogo de casos. Las fichas pendientes por fallos de lectura no se aprueban como
si la fuente se hubiese analizado.

La aprobación registra a la persona revisora y prepara una propuesta privada.
Publicar sigue requiriendo el importador, revisión del cambio y CI del monitor.
El workflow de Gemini carece de acceso de escritura al repositorio público y no
llama al importador.

Referencias: [salidas estructuradas de Gemini](https://ai.google.dev/gemini-api/docs/structured-output)
y [límites de la API](https://ai.google.dev/gemini-api/docs/rate-limits).

El protocolo de [fichas para todas las noticias](NEWS-DOSSIERS.md) describe los estados y la importación con `--kind news_dossier`. El horario configurado debe comprobarse en Actions con el evento `schedule`; las ejecuciones manuales no demuestran que el disparo automático esté funcionando.

## Privacidad por campo y recuperación de fichas anteriores

El prompt `mdh-universal-news-grounded-7-field-privacy` declara `protected_fields`
y el código añade comprobaciones locales por campo. La extracción registra
`privacy_policy_version: 2`. No se conserva el valor ni la cita de un campo
protegido. Las descripciones anónimas se conservan; las menciones generales de
niños o salud no vacían la ficha. Las alertas siguen requiriendo revisión editorial.
La proyección geográfica sólo puede aprovechar departamentos y municipios seguros;
el resto de la ficha continúa privado.

Los borradores antiguos que tienen las 18 casillas protegidas se reextraen
progresivamente, con hasta 5 de los 20 intentos de cada lote (2 de un lote de 10).
Se mantiene una reserva independiente para noticias nuevas. No aumenta el máximo
total de llamadas. Las decisiones humanas se respetan, las versiones anteriores
permanecen en Git y los errores conservan un presupuesto y espera de reintento
propios: una interrupción no abre intentos ilimitados. Los marcadores de finalización
v2 evitan volver a procesar una ficha ya evaluada con la nueva política.
