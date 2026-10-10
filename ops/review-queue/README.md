# Cola editorial privada de MDH-SV

Este repositorio es privado. Las noticias son **candidatas**, no hechos verificados. Puede contener borradores asistidos y citas breves para contrastarlos. No adjuntes cuerpos completos de artículos, fotografías, testimonios privados ni documentos personales.

## Recolección

Actions → **Recoger noticias** se ejecuta cada seis horas (00:17, 06:17, 12:17 y 18:17 UTC; 18:17, 00:17, 06:17 y 12:17 en El Salvador). GitHub puede retrasar las ejecuciones. También puedes usar Run workflow. Las candidatas aparecen en [Issues](../../issues) con `review/pending`. Los enlaces ya revisados o rechazados no se vuelven a abrir.

Se consultan los RSS comprobados de Co Latino, GatoEncerrado y Revista Factum. Se seleccionan títulos/entradas por términos de interés; esto puede producir falsos positivos. Un feed vacío o HTML no cuenta como fuente sana. Cada ejecución informa entradas leídas, candidatas añadidas y fuentes fallidas. Un fallo total hace fallar el job. La recolección RSS no necesita claves de un modelo ni acceso de escritura al repositorio público. La generación asistida usa su configuración separada; sus credenciales deben permanecer en los secretos del repositorio, nunca en Issues o borradores.

## Revisar

1. Abre el enlace de la candidata y revisa el documento completo, fecha, lugar, atribución y actualizaciones. Comprueba que no esté ya publicada y que no sea una actualización de otro caso.
2. Para rechazar: Actions → **Revisar noticia** → Run workflow; indica el número de issue y `reject`. La issue se cierra y queda como `review/rejected`.
3. Abre el borrador privado `drafts/<identificador de candidata>.json`, si existe. Contrasta los campos de `record` y sus referencias con la fuente. La clasificación y las citas de `grounding` ayudan a localizar evidencia, pero pueden contener errores. Corrige y guarda el borrador antes de aprobar. No copies nombres de víctimas menores, direcciones, coordenadas personales ni detalles privados. No inventes fechas ni completes campos desconocidos por inferencia.
4. En **Revisar noticia**, indica el número de issue, elige `approve`, marca la confirmación editorial y deja `record_json` vacío para usar ese borrador. Escribe tus propias comprobaciones en `review_notes` (mínimo 20 caracteres, además de la confirmación de privacidad): fuente leída, hechos y fechas contrastados, duplicados o actualizaciones y decisiones de minimización. Las notas generadas por el modelo no sustituyen estas notas humanas. El proceso exige una cuenta humana con permiso de escritura, vuelve a validar la ficha y registra revisor, fecha e identificador de candidata.
5. La propuesta queda en `approved/<identificador>.json` y en el artefacto privado `propuesta-publica`. La issue pasa a `review/approved`. **Aprobar no publica la noticia.**
6. Desde el repositorio del monitor, ejecuta `python -m mdh_pipeline.review_import --candidate IDENTIFICADOR --repository RauPro/MDH-SV-review`. Usa la autenticación GitHub existente o `GH_TOKEN`; no pegues credenciales en archivos. El importador vuelve a comprobar la aprobación privada, regenera las descargas y deja cambios revisables. Revisa el diff, crea un commit/PR, espera CI y publica siguiendo el protocolo del monitor.

No uses etiquetas manuales como sustituto de la aprobación: el importador exige la propuesta validada. No se notifican ni asignan automáticamente terceras personas. Los fallos de Actions aparecen en este repositorio y se rigen por las preferencias de notificaciones de tu cuenta.

### Estados del borrador y controles de aprobación

Sólo se admite un borrador con `status: draft`, `relevant: true`, clasificación `jurisdiction: SV` y `relevance: relevant`, `publishable: false` y `human_reviewed: false`. Debe contener una ficha con la URL y el identificador de origen de la misma candidata. El archivo se obtiene exclusivamente de `drafts/<id>.json` en esta cola privada; no se descargan archivos desde enlaces suministrados por el borrador.

`needs_scope_review` y `needs_privacy_review` **no se aprueban directamente**. Requieren resolver el alcance o la privacidad contrastando la fuente y preparar una ficha completa para revisión humana. No cambies una bandera para omitir esa comprobación. Una clasificación no pertinente o incierta tampoco rechaza automáticamente la noticia: la decisión sigue siendo editorial.

Un borrador ausente, mal formado, de otra candidata o sin notas y confirmación humana detiene la aprobación sin generar una propuesta ni cambiar la issue. El esquema público rechaza la ficha de origen automatizado mientras no exista revisión humana. La aprobación exporta sólo los campos públicos validados; las citas de trabajo, clasificación, metadatos del modelo y contenido de la issue permanecen privados.

### Preparar una ficha manualmente

Sigue disponible `record_json` para pegar una ficha completa con el formato del [conjunto público](https://github.com/RauPro/MDH-SV/blob/main/data/reviewed/publications.json), manteniendo la URL original de la candidata. Si se proporciona, se usa en lugar del borrador guardado. Puedes escribir las notas en la entrada `review_notes`; para mantener el flujo anterior, una ficha manual también puede incluir sus propias `record.review_notes` de al menos 20 caracteres. Esta compatibilidad no se aplica a las notas del borrador asistido. La confirmación de privacidad y la validación del esquema siguen siendo obligatorias.

Rechazar una noticia no exige ficha, borrador ni notas de aprobación. En ningún caso la generación asistida o la aprobación en esta cola publican por sí mismas en el portal.

## Código y actualizaciones

Los workflows usan un commit fijo del código público. Para actualizarlo, revisa los cambios y sustituye `ref` en los workflows por el commit aprobado. Nunca uses código de pull requests no confiables con el token de esta cola. No hagas público este repositorio ni subas su contenido al portal.
