# Preparar una ficha sin escribir todo el JSON

El operador puede generar una plantilla completa a partir de un candidato privado o de una referencia del archivo histórico. El comando prepara el formato, las referencias y los metadatos; deja los hechos, las personas, los actores y el resto de la investigación sin completar. No lee artículos, no llama a Gemini ni a otro modelo y no publica nada.

## Desde la cola privada

Guarda únicamente el bloque JSON «Metadatos de captura» del candidato en un archivo dentro de `.local-quarantine/`. No es necesario copiar comentarios ni testimonios privados.

```bash
.venv/bin/python -m mdh_pipeline.draft \
  --candidate .local-quarantine/candidato.json \
  --record-type incident
```

## Desde el archivo histórico

Usa el identificador completo de 64 caracteres de la referencia en `apps/web/public/archive/news.json`:

```bash
.venv/bin/python -m mdh_pipeline.draft \
  --history-id IDENTIFICADOR_COMPLETO_DE_LA_NOTICIA \
  --record-type incident
```

El tipo es una decisión del operador: `incident` para un caso individual, `aggregate` para un conjunto documentado y `publication` para un informe de contexto. La herramienta no deduce ese tipo de un titular ni afirma que la noticia tenga jurisdicción salvadoreña.

Si falta la fecha de publicación, el comando se detiene y pide `--published-date AAAA-MM-DD`. Se debe consultar la fuente antes de indicar esa fecha; la fecha de captura nunca la sustituye. `--history-path` permite leer otra copia local del archivo bibliográfico. `--output` sólo acepta archivos JSON dentro de `.local-quarantine/drafts/` y no sobrescribe trabajos previos.

## Completar y revisar

El resultado se guarda con permisos de lectura y escritura sólo para el usuario en `.local-quarantine/drafts/<identificador>.json`, una carpeta excluida de Git. La consola muestra únicamente su ruta y el estado de borrador. El documento contiene:

- `status: draft` y `publishable: false`.
- `candidate_id`, también conservado en `record.origin_candidate_id` como barrera de publicación.
- `completion_required`, una lista de pasos para completar la ficha.
- `record`, la plantilla que se editará y enviará al flujo de revisión humana.

La plantilla es completa, pero **el esquema público rechaza deliberadamente `record` antes de la revisión humana**: contiene `origin_candidate_id` y mantiene `human_reviewed: false`. No elimines esa identidad para sortear la validación. El helper comprueba los demás tipos y campos mediante una copia interna desechable; nunca elimina la barrera del archivo que entrega.

El esquema existente ofrece `source_checked` como estado mínimo, por lo que la plantilla aclara en `source_check_method` que sólo se revisó la coherencia de los metadatos y que el texto está pendiente de lectura. Ese marcador no permite publicar el borrador porque la validación de su identidad de candidata lo impide. La herramienta no atribuye una revisión humana. `penitentiary_related: false` es también un marcador que el operador debe revisar, porque el contrato actual no admite un valor desconocido para ese booleano.

Sustituye el título de borrador, el resumen, la categoría y las etiquetas pendientes. Completa los campos sólo con información atribuible al documento y añade localizadores concretos. Mantén `null` donde no se conoce la información; usa `withheld` y una nota si decides protegerla. Lee [el contrato de dossier](DOSSIER-SCHEMA.md) para ver los tipos y estados. El titular original puede contener información personal: revisarlo sigue siendo obligatorio aunque el helper no extraiga identidades adicionales.

Después de editar, envía **sólo `record`** al campo `record_json` del flujo privado «Revisar noticia». Debe corresponder a un candidato de esa cola; una referencia histórica que aún no esté en la cola requiere incorporarla primero mediante el proceso de revisión. El flujo comprueba y conserva la identidad del candidato, registra los datos de aprobación humana y sólo entonces valida la futura ficha pública. Copiar un borrador sin completar al catálogo no supera la validación. No copies el envoltorio de borrador a `data/reviewed/publications.json` ni lo utilices para omitir ese flujo.

Los metadatos históricos o los bloques copiados no reflejan necesariamente la última decisión de la cola privada. Antes de preparar otra ficha, consulta si ya fue rechazada, aprobada o incorporada a un caso existente.

Para integrar un editor, `prepare_draft(metadata, record_type=...)` devuelve el documento sin escribir en disco. `write_private_draft` aplica la ruta privada y los permisos. No existe opción de extracción mediante IA en este helper: los campos no se adivinan a partir del título.
