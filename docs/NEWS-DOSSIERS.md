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
| `pending` | No se pudo determinar con respaldo suficiente, o todavía no se ha procesado. No afirma que el documento omita el dato. |
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
La privacidad se aplica por campo: conserva descripciones anónimas, fechas y geografía administrativa respaldada, y protege identidades sensibles, contactos, domicilios o datos clínicos. Una alerta no vacía las 18 casillas.
Los errores de lectura o del proveedor dejan una estructura pendiente y su
diagnóstico, sin afirmar que la extracción haya terminado.

La web y `search_news`/`GET /api/v1/news` presentan una estructura para cada
referencia. Los campos se publican por defecto tras validación automática de
evidencia y privacidad. La revisión humana se reserva para solicitudes en GitHub. Un
registro independiente de progreso permite distinguir si la ficha ya se generó,
sin consultar la cola privada desde el navegador ni exponer borradores sin validar.

La publicación automática usa `schema_version: 2`, `human_reviewed: false` y
un recibo `automation` con política, versión de fuente, fecha, modelo evaluador y
`corroboration: single_source`. Una evaluación separada de Gemini contrasta cada
campo con la misma versión del artículo; no constituye una fuente independiente.
El código aplica el resultado por campo, vuelve a minimizar datos sensibles y
no permite restaurar valores protegidos. No crea nuevos valores a partir de la
respuesta del evaluador. Los estados de ausencia se retienen sólo cuando están
respaldados y el texto no está recortado.

Los recibos, diagnósticos y evidencia de evaluación permanecen en el repositorio
privado. Sólo la proyección segura se guarda en `data/automated/news_dossiers.json`
y se combina con las decisiones humanas en `/archive/dossiers.json`. Las decisiones
humanas existentes prevalecen; una retirada persistente elimina la noticia de
las vistas públicas. Los cuerpos de artículos y las citas privadas no se exportan.

## Estado de procesamiento público

`apps/web/public/archive/processing.json` contiene únicamente el identificador,
URL, medio y fecha públicos de la noticia, el hash ya público de su selección y
uno de estos estados:

| Estado | Significado |
| --- | --- |
| `pending` | Pendiente de extracción, confirmado con un inventario privado completo. |
| `generated` | Ficha generada, posiblemente parcial; espera validación automática antes de publicar campos. |
| `source_unavailable` | El intento no pudo acceder a la fuente. |
| `error` | La extracción no se completó correctamente. |
| `unknown` | No se puede confirmar el progreso de esta referencia. |

La API presenta el valor como `processing_status`. Una ficha pública con decisión
humana produce `reviewed`; una ficha v2 válida y vinculada al hash seleccionado
produce `automatically_published`. El archivo de estados no puede conceder ninguna
de esas publicaciones. Un campo no resuelto conserva su propio estado `pending`,
incluso cuando `processing_status` es `generated`. No se convierte en «Dato no
mencionado», porque todavía no hay una afirmación revisada de ausencia.

La proyección privada valida la ficha, identidad bibliográfica y hash de texto.
Un borrador procesado sin correspondencia con la selección actual produce
`unknown`, no `generated`. La clasificación temática, una ubicación automática
o una plantilla vacía no acreditan generación de ficha. Si falta un estado o
falla su carga, la web muestra que no puede confirmarlo; no supone que está pendiente.

El workflow privado de publicación comprueba que el checkout de `drafts/` esté
completo y sin alteraciones antes de usar `--snapshot-complete`. La ausencia de
un borrador en un inventario parcial nunca se convierte en pendiente. No exporta
resúmenes, personas, campos, notas, motivos de privacidad, modelos ni errores
detallados. Usa las mismas credenciales y el mismo horario existentes, sin llamar
a Gemini para preparar estos estados.

```sh
python -m mdh_pipeline.news_processing \
  --drafts /ruta/privada/inbox/drafts \
  --archive apps/web/public/archive/news.json \
  --output apps/web/public/archive/processing.json \
  --snapshot-complete
```

El refresco público de bibliografía sólo elimina estados huérfanos o desactualizados
mediante `node apps/web/scripts/sync-news-processing.mjs`; no inventa estados para
noticias nuevas ni accede a borradores. El build verifica la lista cerrada de campos
y sus vínculos. La fecha de actualización se conserva si no cambia la proyección.
En la interfaz, el mensaje de un campo vacío se muestra una sola vez y el contador
describe «noticias seleccionadas», sin presentarlas como fichas ya generadas.

## Consulta en Fichas y mapa

La vista principal abre la colección **Noticias**, con todas las referencias
listables y acceso a sus fichas. **Reportes documentados** conserva el catálogo
de casos e informes y sus conteos propios. El archivo bibliográfico mantiene la
consulta de cobertura por medio y mes.

El mapa distingue ubicaciones revisadas de ubicaciones automáticas **Sin revisar**.
Las primeras proceden de fichas públicas aprobadas. Las segundas proceden de una
[proyección mínima](AUTOMATIC-LOCATIONS.md) autorizada para departamentos explícitos,
con evidencia y sin aprobar ni publicar los otros campos de la ficha. Una decisión
humana siempre prevalece, incluida la omisión o protección del departamento. No se deduce del título,
la URL, el municipio o las etiquetas de descubrimiento. Los filtros de fechas de
esta colección usan la publicación de la noticia, no la fecha del hecho.

Las noticias sin esa ubicación siguen disponibles en la lista y mediante el
filtro **Sin ubicación pública en El Salvador**. La interfaz distingue revisión
pendiente, jurisdicción por determinar, noticias de otros países y departamento
no disponible. La falta de datos geográficos no significa que el archivo esté vacío. Aprobar una ubicación no convierte una noticia en un caso ni suma
personas al recuento de víctimas.

## Solicitudes en GitHub y correcciones excepcionales

El botón **Reportar un problema** abre una solicitud pública con identificador y
versión, sin copiar los valores de la ficha. El publicador consulta las solicitudes
en cada ejecución. La primera solicitud por noticia añade una reevaluación
persistente; duplicados no reinician intentos ni consumen presupuesto adicional.
El texto libre y sus enlaces no se ejecutan ni se incorporan como evidencia del
modelo. No publicar datos sensibles en una issue; solicitar un canal privado.

Un mantenedor con permiso de escritura puede aplicar las etiquetas
`news/withdraw`, `news/restore` o `news/recheck`. El workflow verifica la identidad
y permisos actuales del actor. Una retirada permanece hasta una restauración
explícita; cerrar una issue o reextraer la noticia no la revierte. Las acciones
quedan registradas en `data/selection/news_exceptions.json`. Una reevaluación no
modifica automáticamente una corrección humana ya importada.

### Corregir una ficha parcial mediante decisión humana

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
node apps/web/scripts/sync-news-publication.mjs
node apps/web/scripts/sync-news-processing.mjs
```

El importador comprueba que la aprobación sigue vigente y que la identidad, URL,
medio y fecha corresponden a la referencia bibliográfica. Guarda solamente la
ficha validada en `data/reviewed/news_dossiers.json` y la exporta a
`apps/web/public/archive/dossiers.json`. No copia el envoltorio privado, comentarios,
diagnósticos, cuerpos de artículos ni credenciales. Los cambios pasan por Git y CI.

Para proponer un caso se mantiene `proposal_kind: case` y el importador anterior
sin `--kind news_dossier`. No se debilitan las reglas del catálogo de casos para
permitir fichas de noticias incompletas.

## Operación y presupuesto

Después del extractor horario, `publish-locations.yml` selecciona las noticias,
sincroniza solicitudes, evalúa fichas en lotes de hasta cuatro artículos por
llamada y publica las proyecciones. El techo de ejecución es 50 llamadas; el cupo
real depende del reparto compartido, del trabajo elegible y del saldo diario.
Con un cupo horario de 10 y trabajo en las tres etapas, el plan reserva 2 llamadas
para clasificación, 6 para extracción y 2 para validar hasta 8 fichas. Al agotarse
la clasificación, ese cupo puede pasar a 8 de extracción y 2 de validación. Las
cantidades son asignaciones, no una promesa de publicación o de plazo del backlog.

`runtime/news-allocation.json` vincula la asignación con la ejecución de extracción
y el día de cuota; el publicador sólo puede reclamarla una vez. Un disparo manual
recibe un cupo proporcional al saldo y las horas restantes, registrado una vez
por ejecución. El saldo no utilizado y confirmado de las etapas anteriores puede
pasar al evaluador. Evaluación, reextracción por fuente cambiada y reintentos
comparten `runtime/gemini-budget.json` con el extractor. No se amplía el tope
propio de 240 solicitudes diarias, 2 por minuto y 200 000 unidades conservadoras
de entrada por minuto, ni se utiliza otro proveedor o una clave para eludirlo. Cada intento HTTP
se reserva antes del envío; un 429 detiene los trabajadores hasta el siguiente
día de cuota. El tope configurado es operativo, no una afirmación de los límites
no publicados por Google. La cuota consumida fuera de estos trabajadores no
puede medirse con este registro local.

Los lotes mantienen párrafos y evidencias aislados por artículo y exigen los
identificadores y hashes exactos en la respuesta. Una fila faltante, una referencia
cruzada o un resultado inválido rechaza el lote completo; puede reintentarse con
grupos menores dentro del mismo presupuesto y límite de intentos. La entrada
completa tiene una cota conservadora de 90 000 unidades, también limitada por el
presupuesto por minuto. Se reduce el grupo antes que recortar la fuente. Cada
ficha conserva un recibo propio y los mismos controles de privacidad; los tokens
agregados del lote no se atribuyen artificialmente a cada noticia. Véase el
[protocolo de lotes y cuotas](GEMINI-REVIEW.md#validación-por-lotes).

Las evaluaciones se reutilizan por hash de borrador, fuente, política y generación
de solicitud. Las fuentes cambiadas se reextraen dentro del mismo presupuesto y
esperan la siguiente selección antes de publicar. Los fallos tienen reintentos
limitados, no un requisito de aprobación humana. La falta de cuota conserva el
trabajo para otra ejecución. No hay garantía de puntualidad exacta del planificador.

Para retirar la vía automática, detener el evaluador y vaciar la colección
automática mediante el reconciliador, conservando las decisiones humanas y el
historial privado. Retirar del sitio no elimina copias ya descargadas por terceros.
