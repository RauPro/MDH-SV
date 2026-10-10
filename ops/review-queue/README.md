# Cola editorial privada de MDH-SV

Este repositorio es privado. Las noticias son **candidatas**, no hechos verificados. No almacena cuerpos completos de artículos, fotografías ni documentos personales.

## Recolección

Actions → **Recoger noticias** se ejecuta cada seis horas (00:17, 06:17, 12:17 y 18:17 UTC; 18:17, 00:17, 06:17 y 12:17 en El Salvador). GitHub puede retrasar las ejecuciones. También puedes usar Run workflow. Las candidatas aparecen en [Issues](../../issues) con `review/pending`. Los enlaces ya revisados o rechazados no se vuelven a abrir.

Se consultan los RSS comprobados de Co Latino, GatoEncerrado y Revista Factum. Se seleccionan títulos/entradas por términos de interés; esto puede producir falsos positivos. Un feed vacío o HTML no cuenta como fuente sana. Cada ejecución informa entradas leídas, candidatas añadidas y fuentes fallidas. Un fallo total hace fallar el job. No hay secretos externos ni acceso de escritura al repositorio público.

## Revisar

1. Abre el enlace de la candidata y revisa el documento completo, fecha, lugar, atribución y actualizaciones. Comprueba que no esté ya publicada y que no sea una actualización de otro caso.
2. Para rechazar: Actions → **Revisar noticia** → Run workflow; indica el número de issue y `reject`. La issue se cierra y queda como `review/rejected`.
3. Para aprobar: prepara la ficha JSON con el formato del [conjunto público](https://github.com/RauPro/MDH-SV/blob/main/data/reviewed/publications.json). No copies nombres de víctimas menores, direcciones, coordenadas personales ni detalles privados. Mantén la URL original de la candidata y añade `review_notes` con la comprobación efectuada. No inventes fechas.
4. En **Revisar noticia**, indica el número de issue, elige `approve`, marca la confirmación editorial y pega la ficha en `record_json`. El proceso exige una cuenta humana con permiso de escritura, valida el esquema y registra automáticamente revisor, fecha e identificador de candidata.
5. La propuesta queda en `approved/<identificador>.json` y en el artefacto privado `propuesta-publica`. La issue pasa a `review/approved`. **Aprobar no publica la noticia.**
6. Desde el repositorio del monitor, ejecuta `python -m mdh_pipeline.review_import --candidate IDENTIFICADOR --repository RauPro/MDH-SV-review`. Usa la autenticación GitHub existente o `GH_TOKEN`; no pegues credenciales en archivos. El importador vuelve a comprobar la aprobación privada, regenera las descargas y deja cambios revisables. Revisa el diff, crea un commit/PR, espera CI y publica siguiendo el protocolo del monitor.

No uses etiquetas manuales como sustituto de la aprobación: el importador exige la propuesta validada. No se notifican ni asignan automáticamente terceras personas. Los fallos de Actions aparecen en este repositorio y se rigen por las preferencias de notificaciones de tu cuenta.

## Código y actualizaciones

Los workflows usan un commit fijo del código público. Para actualizarlo, revisa los cambios y sustituye `ref` en ambos workflows por el commit aprobado. Nunca uses código de pull requests no confiables con el token de esta cola. No hagas público este repositorio ni subas su contenido al portal.
