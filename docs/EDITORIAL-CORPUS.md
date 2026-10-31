# Corpus editorial de evaluación

## Propósito y límites

`data/editorial-corpus/corpus.json` es un conjunto versionado de noticias públicas
etiquetadas para medir decisiones automáticas ya publicadas. Cubre la selección
por pertinencia y jurisdicción, el departamento de las fichas automáticas, las
ubicaciones automáticas y el indicio territorial determinista del título. Sirve
para decidir si un cambio de prompt, política o regla puede aceptarse. No
sustituye la revisión editorial, no certifica hechos y no se publica en la web.

Cada elemento conserva la identidad bibliográfica pública, el SHA-256 del texto
exacto que se etiquetó, los identificadores de párrafo (`pN`, del extractor
`article_source.fetch_article`) y una justificación parafraseada. **No contiene
cuerpos de artículos, citas literales ni nombres de personas.** Las etiquetas se
miden contra el texto con ese hash; si el medio cambia el artículo, la comparación
se excluye como `text_changed` en lugar de atribuir el error al modelo.

Las etiquetas de la versión 1 fueron **propuestas por un asistente de IA**
(`review.proposed_by: ai_assistant`, `review.status: proposed`). No son verdad de
referencia: el informe las muestra como evidencia provisional y **sólo las
etiquetas confirmadas por una persona revisora alimentan los criterios de
aceptación**. El corpus inicial es pequeño; sus cifras tienen mucha incertidumbre.
Por ejemplo, 10 aciertos de 10 son compatibles con una precisión real de sólo
0,72 (límite inferior del intervalo de Wilson al 95 %).

## Muestra

La versión 1 contiene 48 elementos en nueve estratos exclusivos, tomados en este
orden. Dentro de cada estrato se ordena por SHA-256 de
`mdh-editorial-corpus-v1:<candidate_id>`.

| Estrato | Criterio | Tamaño |
| --- | --- | --- |
| `selected-scope-3` | Seleccionada con `mdh-news-scope-3-rights-nexus` | 8 |
| `selected-scope-4` | Seleccionada con `mdh-news-scope-4-inclusive-violence` | 6 |
| `selected-universal-11` | Seleccionada con `mdh-universal-news-grounded-11-inclusive-violence` | 6 |
| `selected-other-prompts` | Seleccionada con `exclusion-1`, `universal-10` o `scope-1` | 3 |
| `automatic-location` | Seleccionada y con ubicación automática | 4 |
| `automatic-dossier-department` | Ficha automática con departamento publicado | 4 |
| `unselected-el-salvador-hint` | Candidata no seleccionada con indicio `el_salvador` | 6 |
| `unselected-international-hint` | Candidata no seleccionada con indicio `international_context` | 5 |
| `unselected-undetermined-hint` | Candidata no seleccionada con indicio `undetermined` | 6 |

Se excluye La Prensa Gráfica porque su acceso automatizado está restringido. Si
una fuente no puede leerse, se toma la siguiente del mismo estrato y la omitida
queda en `sampling.replaced` con un motivo acotado. `sampling.snapshot_commit`
identifica los datos públicos usados. Sobre ese commit, este comando reproduce la
selección previa a las sustituciones:

```sh
.venv/bin/python -m mdh_pipeline.editorial_corpus sample
```

## Guía de etiquetado

Se etiqueta con la política vigente `inclusive-violence-1`
(`SCOPE_POLICY` en `apps/pipeline/src/mdh_pipeline/news_scope_policy.py` y
[NEWS-SELECTION.md](NEWS-SELECTION.md)). Hay que leer **todos** los párrafos del
texto devuelto por el extractor del pipeline. No se consulta la decisión
automática antes de etiquetar, para evitar el anclaje.

| Campo | Valores y criterio |
| --- | --- |
| `jurisdiction` | `SV` si el asunto pertinente tiene una relación sustantiva con El Salvador o con personas salvadoreñas, aunque ocurra en el extranjero. `other` para un asunto de otro país sin ese vínculo. `unclear` si falta información. El medio, su sede, una firma o una mención incidental no bastan. |
| `relevance` | `relevant` para posibles violaciones o contexto de derechos humanos según la política inclusiva. Incluye todos los homicidios vinculados a El Salvador desde 2010, con sus cifras y su seguimiento policial o judicial. No exige vocabulario jurídico, sentencia ni patrón. `not_relevant` sólo para asuntos claramente ajenos, como deportes, entretenimiento o comercio sin vínculo. `unclear` ante duda, texto insuficiente o una incompatibilidad entre la identidad del artículo y su cuerpo. |
| `event_departments` | Departamentos de El Salvador donde el texto sitúa el hecho tratado, de forma explícita o mediante un municipio o distrito inequívoco. Si la nota reúne varios hechos, se usan los pertinentes. Lista vacía si el lugar no consta, si el asunto es nacional sin lugar o si ocurre fuera del país. El cargo, la sede o el domicilio de una persona no sitúan el hecho. |
| `mentioned_departments` | Todos los departamentos nombrados o deducibles de municipios o distritos nombrados, en cualquier contexto. Debe incluir `event_departments`. |
| `evidence_paragraph_ids` | Párrafos `pN` que sostienen la jurisdicción y la pertinencia. Son obligatorios salvo que ambos ejes sean `unclear`. |
| `names_private_persons` | `true` si el texto nombra a personas privadas: víctimas, familiares, testigos, menores o personas detenidas sin proyección pública. **Nunca se registran los nombres.** |
| `rationale` | Paráfrasis breve (10–400 caracteres), sin comillas ni citas literales, sin nombres ni datos que identifiquen a personas. |

Las fechas, los departamentos y la pertinencia se deciden con el texto, nunca con
el título o la URL por sí solos. Una nota con varios hechos es pertinente si al
menos uno cumple la política.

## Revisión humana

1. Abra la fuente original y lea el texto completo. Si el hash actual ya no
   coincide con `text_binding.content_sha256`, anótelo en `review.notes`. Esa
   etiqueta corresponde a la versión anterior del texto.
2. Corrija `label` si procede. **No modifique `proposed_label`**: conserva la
   propuesta para medir su concordancia con la revisión.
3. Establezca `review.status: "confirmed"`, `review.reviewer: "@cuenta"` y
   `review.reviewed_at` en UTC. Use `review.notes` para dudas o desacuerdos.
4. Ejecute las comprobaciones y abra una PR revisada por el responsable temático:

   ```sh
   .venv/bin/python -m mdh_pipeline.editorial_corpus check
   .venv/bin/python -m pytest -q tests/test_editorial_corpus.py
   ```

El validador rechaza una etiqueta propuesta editada sin revisor, una revisión
confirmada sin cuenta, departamentos desconocidos, citas en la justificación,
claves desconocidas o duplicadas y cualquier cambio de identidad respecto de la
bibliografía pública. Cambiar una etiqueta ya confirmada exige otra revisión
documentada en `review.notes`.

## Informe por política y modelo

```sh
.venv/bin/python -m mdh_pipeline.editorial_corpus report
.venv/bin/python -m mdh_pipeline.editorial_corpus report --format json
.venv/bin/python -m mdh_pipeline.editorial_corpus report --decisions /ruta/decisiones.json
```

El informe separa etiquetas confirmadas y provisionales:

- `selection`, por `modelo|prompt_version`: precisión de jurisdicción y de
  pertinencia entre las noticias seleccionadas. Las etiquetas `unclear` no se
  cuentan como acierto ni como error. Los textos distintos del hash etiquetado
  quedan en `text_changed`.
- `dossier_department`, por `modelo evaluador|policy_version`: precisión del
  departamento publicado. `false_absence` cuenta fichas que declaran
  `not_reported` cuando la etiqueta sitúa el hecho en un departamento.
- `locations`, por `método|regla`: `faithfulness` exige que cada departamento
  proyectado esté mencionado en el texto. `event_agreement` es informativo,
  porque la proyección no afirma el lugar del hecho.
- `scope_hint`: matriz del indicio territorial del título, recalculado con el
  código actual (`title-screen-vN`). Permite evaluar un cambio de patrones sin red.
- `unselected`: distribución de etiquetas de candidatas no seleccionadas. **No es
  una medida de exhaustividad**: los snapshots públicos no distinguen pendientes,
  inciertas y excluidas.
- `privacy_inspection`: fichas con personas afectadas publicadas cuando la
  etiqueta indica nombres privados en la fuente. Requieren lectura humana.

`--decisions` acepta un archivo con el formato de `data/selection/news.json`
(`{"selections": [{"candidate_id": ..., "selection": {...}}]}`) sin cuerpos ni
citas. Así se compara una versión candidata con la vigente sobre los mismos elementos.

## Criterios para aceptar cambios

Se aplican a cambios de prompts (alcance, exclusión, extracción o evaluación),
de `SCOPE_POLICY`, de las reglas de publicación automática, de las reglas de
ubicación y del filtro territorial del título.

| Criterio | Umbral | Mínimo de elementos vinculados |
| --- | --- | --- |
| `selection_jurisdiction_precision` | ≥ 0,95 | 10 |
| `selection_relevance_precision` | ≥ 0,90 | 10 |
| `dossier_department_precision` | ≥ 0,90 | 10 |
| `dossier_false_absence` | 0 | 1 |
| `location_faithfulness` | ≥ 0,95 | 10 |

1. Los criterios usan **sólo etiquetas confirmadas**. Una celda por debajo del
   mínimo queda como `insufficient_evidence`: ni aprueba ni bloquea, y el cambio
   necesita la aprobación temática explícita en la PR.
2. Para una versión nueva, ejecútela sobre los mismos textos y obtenga decisiones
   sin cuerpos. Si necesita Gemini, hágalo en la cola privada con su presupuesto.
   Después compare:

   ```sh
   .venv/bin/python -m mdh_pipeline.editorial_corpus report \
     --decisions /ruta/candidata.json --require-gates --cell 'MODELO|PROMPT'
   ```

   La precisión confirmada de la candidata no puede caer más de 0,05 respecto de
   la versión vigente en los mismos elementos, y sus criterios deben pasar.
3. Para el filtro territorial determinista, los recuentos confirmados
   `el_salvador->other`, `international_context->SV` y `undetermined->SV` no pueden aumentar.
4. Un elemento confirmado en `privacy_inspection` debe revisarse antes de aceptar
   el cambio. Una filtración confirmada lo bloquea.
5. Añadir o retirar elementos incrementa `corpus_version` y documenta la muestra.
   **Nunca se corrige una etiqueta para que un criterio pase.**
6. Adjunte a la PR el informe Markdown y el commit de los datos públicos usados.

Los umbrales son la propuesta inicial y deben ratificarse con el responsable
temático; cambiarlos requiere una PR revisada. Un criterio aprobado es una
comprobación mínima sobre una muestra pequeña, no una certificación estadística.

## Limitaciones

- El archivo público sólo contiene selecciones positivas. Medir la exhaustividad
  requiere las decisiones privadas de exclusión e incertidumbre, que no se publican.
- La segunda pasada de Gemini no es una corroboración independiente. Este corpus
  tampoco convierte una noticia en un caso validado.
- Las propuestas del asistente pueden contener errores. Su utilidad es acelerar la
  revisión humana, no sustituirla.
