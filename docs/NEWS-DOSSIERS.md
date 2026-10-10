# Una ficha para cada noticia

Todas las referencias del archivo tienen una ficha estructurada, aunque la fuente
no mencione algunos datos o la noticia no describa una violación de derechos
humanos. Una ficha de noticia no incorpora por sí misma un caso al mapa ni al
recuento del monitor.

La ficha conserva fuente, título y fecha bibliográfica, junto con campos para
resumen, clasificación, presunto hecho violatorio, hechos, personas afectadas,
país, departamento, municipio, lugar público, fecha y hora del hecho, actores,
instituciones, grupos, temas, hipótesis e instrumento. Los campos no desaparecen
cuando falta información y no se completan mediante suposiciones.

## Estados de los campos

| Estado | Presentación y significado |
| --- | --- |
| `pending` | Pendiente de extracción o revisión. No afirma que el documento omita el dato. |
| `reported` | Valor atribuido a la fuente, con evidencia. No equivale a un hecho corroborado. |
| `not_reported` | Dato no mencionado en el texto analizado. La extracción debe señalar expresamente esa ausencia. |
| `withheld` | Dato protegido; se omite su valor y se explica la decisión. |
| `not_applicable` | No aplica a esta noticia; requiere una explicación. |

Los campos sin valor llevan `null`. Una respuesta inválida, un dato descartado
por falta de evidencia y un artículo no accesible permanecen pendientes; no se
convierten en «Dato no mencionado». Si se analizó un texto recortado, la ausencia
se refiere sólo a ese texto y debe contrastarse con la fuente completa.

Los borradores anteriores se conservan. Cuando ya contenían una ficha de caso,
sus valores respaldados pueden migrarse sin volver a llamar a Gemini; los vacíos
antiguos permanecen pendientes. Los resultados anteriores que no extrajeron
campos necesitan un nuevo análisis. Se respetan las decisiones humanas ya tomadas.

## Extracción y publicación

El procesamiento histórico incluye por defecto todos los ámbitos territoriales,
con turnos para referencias salvadoreñas, indeterminadas e internacionales. La
clasificación sirve para consultar y revisar, no para impedir que exista una ficha.
Los límites de lote, cuotas, restricciones de fuentes y reintentos siguen vigentes.

Cada borrador privado guarda `news_dossier`. Puede conservar además `record` como
propuesta opcional de caso; ese formato mantiene sus requisitos más estrictos.
Los problemas de privacidad producen campos protegidos, no una ficha inexistente.
Los errores de lectura o del proveedor dejan una estructura pendiente y su
diagnóstico, sin afirmar que la extracción haya terminado.

La web y `search_news`/`GET /api/v1/news` presentan una estructura para cada
referencia. Cuando aún no hay una ficha aprobada y publicada, muestran campos
pendientes. No consultan el repositorio privado ni revelan su contenido o su
estado individual. Por ello ese estado público no permite concluir si Gemini
ya terminó una extracción privada.

## Aprobar una ficha parcial

1. En la cola privada, abra `drafts/<id>.json` y revise `news_dossier` con el texto
   original. Puede corregir los campos y mantener los datos no mencionados como
   tales. Revise también las identidades y lugares que deban protegerse.
2. En **Revisar noticia**, elija `proposal_kind: news_dossier`, confirme la revisión
   editorial y de privacidad y escriba sus comprobaciones en `review_notes`.
   Una noticia internacional o sin un presunto hecho violatorio puede aprobarse
   como ficha de noticia; no se convierte en un caso salvadoreño.
3. La propuesta validada queda en `approved-news/<id>.json`. Aprobarla en privado
   todavía no la publica. No se puede aprobar una lectura fallida como si hubiese
   sido completada; puede prepararse una ficha manual tras consultar la fuente.
4. Importe la propuesta y revise el cambio público:

```sh
python -m mdh_pipeline.review_import --kind news_dossier --candidate IDENTIFICADOR
python -m mdh_pipeline.news_publication
```

El importador comprueba que la aprobación sigue vigente y que la identidad, URL,
medio y fecha corresponden a la referencia bibliográfica. Guarda solamente la
ficha validada en `data/reviewed/news_dossiers.json` y la exporta a
`apps/web/public/archive/dossiers.json`. No copia el envoltorio privado, comentarios,
diagnósticos, cuerpos de artículos ni credenciales. Los cambios pasan por Git y CI.

Para proponer un caso se mantiene `proposal_kind: case` y el importador anterior
sin `--kind news_dossier`. No se debilitan las reglas del catálogo de casos para
permitir fichas de noticias incompletas.
