# Procesamiento privado de noticias de MDH-SV

La consulta pública aplica el [protocolo de selección temática](https://github.com/RauPro/MDH-SV/blob/main/docs/NEWS-SELECTION.md).
Las candidatas se reconstruyen desde los checkpoints, no desde el archivo público
filtrado. `extract.yml` clasifica candidatas y completa fichas de las seleccionadas, con
un único presupuesto horario repartido entre etapas según el trabajo elegible.
La clasificación avanza a la vez que se extraen y validan noticias ya seleccionadas;
no hace falta terminar el archivo completo para publicar fichas. `review-scope.yml` queda para pruebas
manuales, sin otro disparo automático. Conserva evaluaciones en `scope-reviews/`
y reintentos diferidos en `scope-attempts/`.
El publicador usa esas decisiones para actualizar selección, noticias y mapa.
Las referencias ya seleccionadas se sincronizan con la cola privada en lotes de
hasta 50 altas por ejecución, con pausas entre escrituras. Esa sincronización no
consume llamadas a Gemini ni aumenta el cupo de extracción de fichas. Una noticia
seleccionada puede tener todavía su extracción pendiente; una ficha extraída
necesita validación automática de evidencia y privacidad antes de publicar sus campos. La intervención editorial se reserva para solicitudes en GitHub.
La ubicación se comprueba también directamente en las fuentes seleccionadas,
sin esperar una ficha completa ni llamar de nuevo a Gemini. Comprueba hasta
50 fuentes por ejecución y conserva recibos mínimos de reintento en una caché
privada de Actions; no almacena allí cuerpos, citas ni campos personales.

El publicador también genera `/archive/processing.json` después de comprobar que
el inventario privado de borradores esté completo y sin alteraciones. Sólo exporta
la identidad bibliográfica pública y el estado `pending`, `generated`,
`source_unavailable`, `error` o `unknown`; no copia campos, motivos de privacidad
ni diagnósticos privados. Esto permite distinguir una ficha ya generada de una
extracción pendiente en la web y API. Una ficha aprobada siempre prevalece.
El estado `generated` no autoriza publicar por sí solo. Una ficha v2 válida produce `automatically_published` y conserva `human_reviewed: false`; una decisión humana importada produce `reviewed`.

Este repositorio es privado. Todas las noticias recogidas tienen una estructura
de ficha, aunque falten datos, el ámbito territorial sea incierto o la noticia no
describa una violación. Una ficha no acredita los hechos ni se publica por sí sola.
No adjuntes cuerpos completos de artículos, fotografías, testimonios privados ni
documentos personales.

## Generación y estados

**Preparar fichas con Gemini** tiene un respaldo de GitHub cada hora al minuto 23.
El disparador principal de Vercel lo solicita al minuto 07; su
instalación y configuración están descritas en el [protocolo horario](https://github.com/RauPro/MDH-SV/blob/main/docs/HOURLY-INGESTION.md).
Recoge RSS, incorpora hasta 50 referencias históricas y solicita hasta 50 noticias
por lote. El presupuesto configurado reduce ese máximo y cuenta cada solicitud
y reintento. Si las cuotas de Google no son visibles, `operator_cap` identifica
topes propios preventivos; un 429 pausa las llamadas hasta el siguiente día del
Pacífico. Estos topes no son cuotas confirmadas del proveedor.
`runtime/gemini-hourly.json` evita lotes duplicados en la misma hora UTC, incluso
entre ejecuciones manuales y automáticas.
Los RSS de Co Latino, GatoEncerrado y Factum y el ingreso histórico independiente
también tienen horarios cada seis horas. Comprueba en Actions los eventos
`schedule` y los registros Cron de Vercel: una ejecución manual exitosa no verifica
el disparo del horario.

El histórico incluye por defecto todos los ámbitos territoriales y reserva turnos
para que las referencias internacionales o por determinar puedan avanzar. Las
fuentes bloqueadas no se eluden. Los fallos tienen diagnósticos y reintentos
acotados; la estructura pendiente no se confunde con una extracción terminada.

`drafts/<id>.json` contiene `news_dossier`. Puede incluir además `record`, una
propuesta de caso con requisitos más estrictos. Los datos desconocidos no se
rellenan mediante suposiciones:

- `pending`: campo no evaluado o pendiente de contraste.
- `not_reported`: dato expresamente no mencionado en el texto analizado.
- `withheld`: valor protegido y omitido.
- `not_applicable`: campo que no corresponde, con explicación.
- `reported`: valor atribuido a la fuente, con referencia de evidencia.

Los borradores antiguos con datos reutilizables se migran sin una llamada al
modelo. Sus vacíos no se transforman en ausencias declaradas. Los resultados que
antes no extrajeron campos pasan por el nuevo análisis. Git conserva las versiones
anteriores y se respetan las decisiones humanas existentes.

## Evaluación y publicación automática

`publish-locations.yml` conserva el nombre histórico del publicador e incluye ahora:

1. Exclusiones editoriales y retiradas persistentes; reconstrucción de la selección.
2. Verificación del inventario privado y proyección de estados mínimos.
3. Lectura acotada de solicitudes GitHub y evaluación de fichas generadas con
   `automatic_news evaluate`. Recibos y reintentos quedan en `automatic-news/`.
4. Proyección de campos mediante `automatic_news export`, seguida de comprobaciones
   geográficas independientes que no llaman a Gemini.
5. Conciliación de snapshots con `sync-news-publication.mjs` y
   `sync-news-processing.mjs`, validación de datos y auditoría del commit permitido.
6. Push al repositorio público, del que Vercel despliega web, REST y MCP.

El límite de evaluación es un techo de llamadas por ejecución, no un presupuesto
nuevo. `automatic_validation_limit` vale 50 por defecto; cada solicitud puede
evaluar hasta cuatro fichas. La plantilla usa `VALIDATION_BATCH_SIZE=4` y
`VALIDATION_LIMIT=50`, que pasan a `automatic_news evaluate --batch-size 4 --limit 50`.
Clasificación, extracción, evaluación y reintentos comparten
`runtime/gemini-budget.json`, el tope propio de 240 solicitudes diarias, 2 por minuto,
200 000 unidades conservadoras de entrada por minuto y las pausas
ante 429. Ese tope no es una cuota confirmada por Google. No se activa facturación,
no se cambia de modelo al agotar saldo y los disparos manuales no reinician el contador.

El extractor registra el reparto en `runtime/news-allocation.json`. Con un cupo
horario de 10 y trabajo en las tres colas, el plan asigna 2 llamadas a clasificación,
6 a extracción y 2 a validación de hasta 8 fichas. Sin clasificación pendiente,
ese cupo puede pasar a 8 de extracción y 2 de validación. Si una etapa queda sin
trabajo, el reparto se adapta, y la capacidad no utilizada confirmada de las
etapas anteriores puede pasar a validación. No existe un límite fijo de 3 fichas
por hora ni una garantía de publicar el máximo teórico.

`MDH_ALLOCATION_SOURCE_RUN_ID` identifica la ejecución de extracción que generó
la asignación. El publicador la reclama una sola vez y sólo dentro del mismo día
de cuota; si falta, caducó o ya fue usada, no obtiene otra asignación como respaldo.
Sin ejecución anterior asociada, se registra un cupo proporcional al saldo y
las horas restantes una sola vez por `GITHUB_RUN_ID`. El registro de reparto
no sustituye las reservas obligatorias antes de cada intento HTTP.

El lote tiene una cota de entrada de `min(90_000, input_tpm - 1_024)`, calculada
sobre todo el JSON serializado en UTF-8 más un margen de 1 024 unidades. No es
una medición exacta de tokens. Se conservan todos los párrafos del texto recuperado
y se reducen los grupos si no caben. Cada artículo usa referencias exclusivas
como `n1-p1`; un hash incorrecto, identificador faltante o referencia cruzada
rechaza la respuesta completa. Los errores de formato pueden dividir el grupo
para reintentar, sin aumentar presupuesto, intentos permitidos ni tiempo máximo.
Los recibos y las protecciones siguen siendo individuales; `usage` queda nulo
para no inventar consumo por artículo a partir de tokens agregados del lote.

Los resúmenes de ejecución exponen `validation_request_grant`, `http_requests`,
`evaluated`, `publishable`, `batch_sizes` y `recovery_splits`. Comprobar esos valores
y la posterior publicación antes de estimar la velocidad o el fin del backlog.

Sólo se evalúa contra el mismo hash que extracción y selección. Un cambio de fuente
requiere actualizar esos vínculos antes de publicar. Una ficha puede ser parcial;
los campos dudosos quedan pendientes y los protegidos se omiten. La conciliación
conserva también las protecciones de títulos y geografía en las descargas públicas.
La publicación automática nunca añade registros al catálogo de casos ni modifica
cifras oficiales. Véase [NEWS-DOSSIERS.md](https://github.com/RauPro/MDH-SV/blob/main/docs/NEWS-DOSSIERS.md).

## Corregir una ficha por solicitud humana

Las fichas ordinarias se publican automáticamente. Este procedimiento se conserva
para una corrección editorial solicitada; no es un requisito para vaciar el backlog.
La primera solicitud pública por noticia permite una reevaluación acotada. La
explicación y sus adjuntos no se ejecutan ni se envían al modelo; sólo pasan el
identificador y la generación de solicitud. Un mantenedor autorizado puede
solicitar nuevas reevaluaciones o una retirada persistente.

1. Abre la fuente original y `news_dossier` en el borrador. Contrasta la fecha,
   hechos, campos no mencionados, atribuciones, duplicados y privacidad. Corrige
   el JSON si corresponde; no necesitas inventar valores para completar campos.
2. En **Revisar noticia**, selecciona la issue, `approve` y
   `proposal_kind: news_dossier`. Confirma la revisión de privacidad y escribe tus
   comprobaciones en `review_notes` (mínimo 20 caracteres). Deja `record_json`
   vacío para usar el dossier guardado o proporciona el dossier corregido.
3. La propuesta validada queda en `approved-news/<id>.json` y en el artefacto
   privado `propuesta-publica`. La noticia pasa a `review/approved`. Esto todavía
   no publica contenido.
4. En el repositorio público del monitor, ejecuta:

```sh
python -m mdh_pipeline.review_import --kind news_dossier --candidate IDENTIFICADOR
```

El importador comprueba aprobación vigente, identidad y bibliografía, y prepara
los archivos revisables. Revisa el diff, crea commit/PR y espera CI antes de
publicar la corrección humana, que prevalece sobre la ficha automática. Comentarios,
diagnósticos y envoltorios privados no se exportan.

Una ficha parcial o internacional puede aprobarse como ficha de noticia. Un fallo
de lectura no puede aprobarse como lectura completada: primero consulta la fuente
y prepara o corrige su ficha. Para rechazar, elige `reject`; no se exige una ficha.

## Proponer un caso

`proposal_kind: case` conserva las reglas de jurisdicción, pertinencia y esquema
del catálogo. Usa `record` del borrador o un JSON manual completo. Las propuestas
quedan en `approved/<id>.json`; el importador anterior sin `--kind news_dossier`
incorpora casos. Las fichas de noticias nunca se suman automáticamente a ese catálogo.

## Código, credenciales y publicación

Los workflows usan un commit fijo del código público revisado. `GEMINI_API_KEY`
permanece en Actions Secrets y `GEMINI_MODEL` selecciona el modelo. La variable
`MDH_GEMINI_BUDGET_CONFIG` contiene los límites y su procedencia, sin secretos;
`runtime/gemini-budget.json` registra reservas de solicitudes sin contenido periodístico.
Sin configuración o registro válidos, el procesador no llama a Google.
La clave Gemini no se guarda en Issues, fichas, archivos públicos ni Vercel.
El disparador Vercel utiliza una credencial separada de GitHub App con Actions:write
para este repositorio. El evaluador es una segunda pasada de Gemini sobre la
misma fuente, no una corroboración independiente. El motor de reglas decide qué
campos conservar después de validar el resultado. El publicador actualiza
`data/selection/news.json`, `data/automated/news_dossiers.json` y los snapshots
públicos de bibliografía, fichas, ubicaciones y progreso. Combina las fichas v2
con las decisiones humanas sin atribuir revisión humana a la automatización.
La lista de archivos permitidos es explícita; no exporta cuerpos, borradores,
comentarios ni datos sensibles protegidos. Se ejecuta después de extracción,
reevaluación de pertinencia o revisión exitosa. Puede recuperar resultados ya
validados de un extractor fallido de `main` del mismo repositorio; la alerta
permanece y nunca se aplica esa excepción a una revisión humana fallida.
Las decisiones humanas prevalecen.

No hagas público este repositorio. Para detener todas las llamadas nuevas a
Gemini, desactiva el extractor y el publicador con evaluación automática, además
de evitar los disparos manuales de pertinencia. Los borradores se conservan. No se notifican ni asignan terceras
personas automáticamente. [Protocolo de fichas](https://github.com/RauPro/MDH-SV/blob/main/docs/NEWS-DOSSIERS.md).

La privacidad actual protege casillas concretas y conserva los campos seguros.
Los borradores antiguos totalmente ocultados se reextraen por lotes, reservando
hasta 5 intentos por lote, sin superar el límite total ni repetir una decisión
humana. El publicador admite municipios o distritos citados y resueltos mediante
la referencia administrativa oficial, con procedencia explícita; no recupera
geografías protegidas ni convierte sedes o nombres ambiguos en lugares de hechos.
