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
  referencias de contexto internacional se excluyen del ingreso histórico
  ordinario. No se promete una fecha de finalización: depende de cuotas, fuentes,
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

- `drafts/<id>.json`: ficha propuesta o resultado que requiere aclarar alcance o
  privacidad; modelo, versión del prompt, fecha, hash del texto de entrada,
  párrafos de referencia, hashes de citas, commit del procesador y ejecución de Actions. Incluye el uso de tokens cuando Google
  lo proporciona. No contiene el cuerpo periodístico completo.
- `processing/<id>.json`: estado, número de intentos y código de error sanitizado.
- La issue original: enlace al borrador y una etiqueta de resultado.

Los valores desconocidos permanecen nulos. Una noticia de pertinencia ambigua no
se convierte forzosamente en un caso. Los textos con riesgos sensibles se derivan
a revisión de privacidad y pueden quedar sin ficha utilizable hasta intervención
humana. Estos resultados siguen en la cola; el modelo no los rechaza ni aprueba.

## Estados, errores y reanudación

| Etiqueta | Acción editorial |
| --- | --- |
| `ai/ready` | Contrastar y corregir la ficha estructurada. Sigue sin aprobar. |
| `ai/scope-review` | Determinar pertinencia y jurisdicción; no hay un caso validado. |
| `ai/privacy-review` | Revisar la protección de personas antes de preparar una ficha. |
| `ai/error` | Revisar código de error y fuente. No se creó una ficha de sustitución. |

Las respuestas tienen un máximo de 4.096 tokens y 192 KiB. El cliente limita
reintentos de red, 429 y 5xx a tres; el lote se detiene ante cuota, autenticación o configuración
inválida del proveedor. Los fallos ordinarios se reintentan con espera y un máximo de tres
intentos por candidata. `retry_failed` en la ejecución manual permite intentar
otra vez tras corregir la causa. Nunca sobrescribe un borrador ya guardado.

El índice privado y los estados evitan repetir llamadas al modelo al reanudar.
Si el job se interrumpe después de guardar una ficha, la siguiente ejecución puede
reparar el enlace de la issue sin otra llamada. Los casos aprobados, rechazados o
cerrados se respetan. Un fallo entre la respuesta de Google y su persistencia
puede requerir repetir esa llamada; no hay garantía transaccional entre servicios.

Los fallos de fuente, validación o proveedor se informan con códigos y contadores,
sin volcar textos ni errores de validación que pudieran contenerlos. El resumen de
Actions siempre incluye `publications: 0`.

## Revisión y publicación

El borrador mantiene `human_reviewed:false`, `publishable:false` y
`origin_candidate_id`; el esquema público lo rechaza en ese estado. Un editor abre
la fuente, revisa los localizadores y modifica la ficha si corresponde. Luego usa
**Revisar noticia**, confirma la comprobación de privacidad y redacta sus notas.
Puede dejar `record_json` vacío para usar el archivo privado, o proporcionar una
ficha corregida. Una respuesta que pide revisión de alcance o privacidad no se
aprueba directamente como ficha.

La aprobación registra a la persona revisora y prepara una propuesta privada.
Publicar sigue requiriendo el importador, revisión del cambio y CI del monitor.
El workflow de Gemini carece de acceso de escritura al repositorio público y no
llama al importador.

Referencias: [salidas estructuradas de Gemini](https://ai.google.dev/gemini-api/docs/structured-output)
y [límites de la API](https://ai.google.dev/gemini-api/docs/rate-limits).
