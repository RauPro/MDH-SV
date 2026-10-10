# Fichas estructuradas de investigación

Cada registro del catálogo incluye `dossier.schema_version: 1`. Las fichas recuperan los campos de la implementación anterior de WSDS (clasificación, hechos, lugar, fuentes, temas, hipótesis, población vulnerable, instrumento, personas afectadas y actores). Esa correspondencia procede de la implementación original inspeccionada en el entorno local durante la migración, incluido su generador de instrucciones. El directorio local `legacy/` está excluido de Git y no se ofrece como referencia pública disponible en el repositorio. No se atribuye una lectura del texto completo del artículo IEEE, que requiere acceso por suscripción.

La estructura pública minimiza datos personales y exige referencias para cada valor. Una noticia, un reporte colectivo y un informe de contexto siguen siendo tipos distintos. No se convierte una noticia automáticamente en un hecho probado.

## Contrato de cada campo

```json
{
  "value": "Detención provisional prolongada cuestionada por la fuente",
  "status": "reported",
  "evidence_refs": ["primary"],
  "note": "Atribución a la fuente; no determina responsabilidad individual."
}
```

| Estado | Significado |
| --- | --- |
| `reported` | Valor atribuido a una fuente declarada; requiere valor y referencia. No significa «confirmado». |
| `not_reported` | Valor no consignado en la ficha. No afirma que el hecho no ocurrió ni que el documento carezca de él. |
| `withheld` | Dato deliberadamente omitido para proteger a las personas; requiere explicación. |
| `not_applicable` | Campo que no corresponde al tipo de registro; requiere explicación. |

Los tres últimos estados siempre llevan `value: null`. Una lista vacía no se usa como sustituto de una ausencia desconocida. `evidence_refs` contiene identificadores de `dossier.evidence`, cuyas entradas son `{id, url, locator}`. Una misma URL puede tener varios localizadores, por ejemplo la metodología, un testimonio o una sección concreta. Las URL deben corresponder a `source_url` o a una fuente declarada en `related_sources`; un enlace nuevo exige incorporarlo primero al registro.

## Campos

| Campo | Tipo del valor | Uso |
| --- | --- | --- |
| `alleged_violation` | texto | Conducta o derecho afectado atribuido a la fuente; las decisiones judiciales se identifican como tales. |
| `facts` | texto | Síntesis fáctica, incluidas actualizaciones respaldadas por fuentes adicionales. |
| `affected_persons` | lista de textos | Persona o grupo en su forma pública y minimizada; no constituye un padrón de víctimas. |
| `location.department` | texto | Departamento del hecho, cuando se puede atribuir. |
| `location.municipality` | texto | Municipio expresamente respaldado, sin deducirlo de una ciudad, un distrito o una división administrativa anterior. |
| `location.public_place` | texto | Lugar público relevante, como un centro de detención; nunca un domicilio preciso. |
| `event_date`, `event_date_end` | fecha ISO o null | Fecha o límites de intervalo; la precisión se conserva en `date_precision`. |
| `event_time` | texto | Hora expresamente indicada; no se deriva de la hora de publicación. |
| `reported_actors` | lista de textos | Instituciones o actores mencionados en relación con los hechos; no es una declaración de culpabilidad. |
| `institutional_sources` | lista de textos | Autoría documental y fuentes institucionales/testimoniales citadas, diferenciando acceso directo e indirecto en la nota. |
| `vulnerable_groups` | lista de textos | Grupos expresamente respaldados por el contenido, sin inferencias sobre identidades privadas. |
| `themes` | lista de textos | Etiquetas para consulta; no son conclusiones jurídicas. |
| `hypothesis` | texto | Interpretación expresamente atribuida a la fuente; no se genera una hipótesis propia. |
| `instrument` | lista de textos | Arma o instrumento expresamente descrito, incluido el software de vigilancia. |

La clasificación general, el título y las fechas de publicación permanecen en los campos existentes para preservar compatibilidad. `incident_date`, `incident_date_end`, `date_precision` y `department` deben coincidir con el dossier; la validación rechaza contradicciones. Las fechas inicial/final de un intervalo mensual o anual son límites de búsqueda, no días exactos acreditados: siempre se deben mostrar junto con la precisión y `date_note`.

Un informe general no recibe ubicación ni fecha de incidente: estos campos tienen estado `not_applicable`. Las fichas de menores conservan una descripción general de la persona y su departamento; se omiten nombre, edad exacta, municipio y localidad. La fecha consignada proviene de la fuente y no identifica por sí sola una revisión independiente. No se exportan diagnósticos ni domicilios.

## Consulta de fuentes y alcance inicial

Se estructuraron los 24 registros ya publicados. Entre los pasajes contrastados están el resumen y los casos asociados a las notas 276–285 de *Su hijo no existe aquí* de HRW; el reporte de HRW sobre Ruth López del 18 de mayo de 2026; el de Henríquez y Pérez del 18 de septiembre de 2025; el comunicado 194/25 de la CIDH sobre Anaya; la actualización AMR 29/1479/2026 de Amnistía sobre Zavala; la comunicación AL SLV 2/2024 de los procedimientos especiales de la ONU; el Proyecto Torogoz de Citizen Lab; y las secciones sobre el CECOT de *Llegaron al infierno*. Cada ficha enlaza su documento y el localizador correspondiente.

El comunicado de la CIDH también se puede consultar mediante su [vista oficial alternativa](https://www.oas.org/es/CIDH/jsForm/?File=%2Fes%2Fcidh%2Fprensa%2Fcomunicados%2F2025%2F194.asp). La lectura automatizada de los dos comunicados PDF de la Corte IDH no respondió durante esta ampliación; sus fichas conservan la información ya documentada, sin añadir detalles biográficos ni clínicos.

Esta edición mantiene `review_status: source_checked` y `human_reviewed: false`. La consulta documental asistida no es revisión editorial humana ni corroboración independiente. Los campos desconocidos no se rellenan con supuestos. No se añadieron nuevos casos mediante esta migración de estructura.

## Exportaciones e incorporación de nuevas fichas

JSON y GeoJSON conservan el objeto anidado. CSV y Parquet incluyen la columna `dossier` como JSON determinista con claves ordenadas, que se puede recuperar mediante `json.loads`. La geometría sigue siendo nula: no se inventan coordenadas a partir de un departamento.

Las propuestas de publicación nuevas deben incluir el dossier. Al adaptar una ficha de ejemplo, hay que sustituir hechos, atribuciones y referencias, no sólo la URL principal: la validación rechaza referencias que no correspondan a las fuentes declaradas. El flujo de revisión humana de las noticias recolectadas permanece obligatorio antes de importar una propuesta.

```bash
.venv/bin/python -m mdh_pipeline.publication
.venv/bin/pytest -q tests/test_publication.py tests/test_dossier.py
```

El modelo ejecutable está en `apps/pipeline/src/mdh_pipeline/publication.py`. Las pruebas comprueban atribuciones, referencias, coherencia de fechas y lugar, minimización de datos de menores y conservación íntegra en las cuatro exportaciones.
