# Selección temática de noticias

El monitor publica posibles afectaciones de derechos humanos y contexto
sustantivo de su ejercicio, protección, defensa, reparación o memoria histórica,
con vínculo con El Salvador. Incluye leyes, políticas, investigaciones y trabajo
de defensores; no exige una violación probada ni una víctima identificada.
Incluye los reportes de homicidios vinculados a El Salvador o a personas
salvadoreñas desde 2010: hechos, cifras, tendencias y seguimiento policial o
judicial. No exige otra denuncia de abuso ni la expresión «derechos humanos».
Clasificar una noticia como pertinente no acredita sus hechos ni determina
responsabilidad jurídica. Deportes, entretenimiento y asuntos sin vínculo
sustantivo siguen fuera; la duda mantiene una candidata pendiente.

## Descubrimiento y publicación

La búsqueda amplia conserva referencias y cobertura en `data/history/`, dentro
del repositorio, y prepara candidatas para la cola privada. Esos metadatos de
descubrimiento no son el archivo seleccionado de la web. Excluir una noticia de
la consulta no borra su candidatura ni su ficha privada.

`history --export-only --candidates-only --output candidates.json` construye el
archivo de trabajo fuera del sitio. La exportación ordinaria exige el manifiesto
`data/selection/news.json` y las decisiones editoriales. Sólo publica referencias
con pertinencia y vínculo territorial respaldados por el texto, y recalcula los
recuentos del archivo, los medios y los meses. Interfaz, JSON descargable, REST,
MCP y mapa consultan esa misma selección. Un indicio del título nunca autoriza
por sí solo la publicación.

Cada referencia tiene `selection`. `gemini_grounded_scope` conserva modelo,
versión del criterio, fecha, hash del texto y localizadores de los párrafos de
alcance y pertinencia. Sigue `automated_unreviewed`: es una clasificación
automatizada, no una corroboración de los hechos ni una aprobación de la ficha.
No incluye cuerpos, citas literales ni campos personales del borrador.
`editorial_scope` requiere una ficha aprobada y coincidente en identidad, revisor
y fecha; una decisión humana negativa o incierta prevalece.

Para retirar una ficha humana ya importada, registra la decisión en el workflow
`Revisar noticia` y actualiza explícitamente `data/reviewed/news_dossiers.json`
mediante un cambio editorial revisado, regenerando los snapshots y comprobando
CI. Rechazarla sólo cierra y etiqueta el Issue privado: ese workflow no modifica
la fuente pública revisada. El importador tampoco reemplaza una ficha existente
con una propuesta distinta; exige resolver el cambio explícitamente. Si la fuente
revisada todavía contiene una ficha positiva excluida del archivo, las
comprobaciones de integridad detienen la publicación y permanece el snapshot
anterior. Una etiqueta por sí sola no garantiza la retirada del sitio.

Clasificaciones `not_relevant`, `other` o `unclear`, evidencia ausente y
contradicciones quedan fuera. Un fallo de validación no vuelve a mostrar todos
los candidatos. Los campos faltantes no impiden por sí solos seleccionar una
noticia: pertinencia, privacidad, ficha completa y validación de un caso son
controles diferentes.

## Clasificación antes de las fichas

La política `mdh-news-scope-4-inclusive-violence` clasifica directamente
cualquier referencia tras leer su fuente: no exige ficha, issue ni etiqueta
positiva anterior. Incluye hacinamiento y reinserción como contexto cuando el
artículo trata sustantivamente esas cuestiones, sin convertir una política
pública en una denuncia de violación. Las fichas nuevas usan
`mdh-universal-news-grounded-11-inclusive-violence`. La evaluación de alcance v4
prevalece sobre la clasificación auxiliar de una ficha del mismo texto.
Una noticia policial o judicial no se descarta por su formato: homicidios,
violencia de género, niñez, desapariciones y muertes bajo custodia son pertinentes.
También se admite contexto laboral, vivienda, agua, salud, migración y garantías.
Basta un hecho pertinente dentro de una nota de varios sucesos. El vínculo con
El Salvador debe corresponder a ese asunto; no se inventa a partir del medio.

El job programado clasifica primero y completa fichas después, bajo una única
reserva horaria. Con diez solicitudes, el reparto normal es dos para alcance,
seis para extracción y dos para validar fichas en lotes de hasta cuatro. Las
dos de alcance se comparten entre primera clasificación y segunda validación
de descartes; no se suman por encima de la reserva. La primera solicitud puede
clasificar hasta dieciséis artículos, reduciendo el lote según el tamaño
del JSON completo, con el mismo máximo de 100.000 unidades conservadoras de
entrada. La fase tiene hasta 720 segundos para aprovechar su asignación sin
aumentar solicitudes. El máximo de salida sigue en 4.096 tokens; una respuesta
incompleta no publica evaluaciones parciales. Se preserva un intento para RSS nuevo antes del backlog
positivo. Los reintentos también consumen presupuesto.

La validación local exige la cantidad y los identificadores exactos del lote,
rechaza duplicados y comprueba la evidencia. Si una respuesta tiene errores estructurales, el trabajador puede
dividir el lote y reevaluar sus mitades con la misma reserva y plazo. Nunca
repara identificadores ni aprovecha filas de la respuesta inválida. Un 429,
una pausa de presupuesto o un error del proveedor no dispara esta subdivisión.
Los errores no recuperados mantienen la ejecución en fallo y los contadores
indican cuántos artículos se recuperaron o siguen pendientes.

Un fallo del extractor no impide proyectar los lotes válidos ya guardados: el
publicador vuelve a validar los documentos de `main` con código fijado, revisa
las fuentes de las ubicaciones y conserva todos los controles editoriales y de
privacidad antes de publicar. Esta excepción exige que la ejecución fallida sea
`extract.yml` del mismo repositorio privado y de `main`; no se aplica a revisiones
editoriales fallidas, cancelaciones ni ejecuciones agotadas por tiempo. No se
descargan código, logs ni artefactos de la ejecución fallida y su alerta permanece.

Las decisiones quedan en `scope-reviews/`; v2 conserva un recibo de lectura con
fecha, método, truncamiento y hash, sin cuerpo. Las evaluaciones con políticas
negativas anteriores se reanalizan bajo la política inclusiva. Los positivos
v3 válidos se conservan. Las solicitudes auditadas tienen prioridad sin
reiniciar el contador de uso. Una ficha
puede conservar otro snapshot: no bloquea la clasificación de una lectura nueva
ni convierte los datos antiguos en campos aprobados.

Las cachés se cargan una vez por ejecución, sin miles de consultas individuales
a GitHub. Las fuentes inaccesibles tienen reintentos diferidos: 24 horas para
fallos ordinarios y siete días para acceso restringido, sin eludirlo. Los hashes
describen lo consultado; una caché no demuestra que la página no haya cambiado.

`review-scope.yml` queda para pruebas manuales; no duplica automáticamente el
presupuesto del job programado. Todos comparten el contador persistente de Gemini y
la pausa global ante 429. No se eleva el tope diario ni se habilita facturación.

## Correcciones automáticas trazables

Una negativa inicial no es un descarte definitivo. La segunda evaluación usa
`mdh-news-exclusion-1-inclusive-violence`, en una petición separada de Gemini,
sin recibir el veredicto anterior y con hasta ocho artículos de texto completo.
Sólo dos decisiones concordantes sobre el mismo eje negativo pueden excluir.
La segunda debe identificar párrafos del asunto efectivamente tratado. Un cambio
de texto, entrada truncada o discrepancia mantiene pendiente; si la segunda
identifica pertinencia salvadoreña, se conserva esa evaluación positiva.

Los recibos de `scope-verifications/` vinculan ambas evaluaciones, el hash del
texto, el hash de la primera decisión, versiones, fechas y motivos acotados.
No guardan cuerpos ni citas. Una segunda consulta al mismo proveedor no es
corroboración independiente de los hechos y no garantiza ausencia de errores.
Las discrepancias se reintentan con una pausa de siete días; los reintentos
consumen el mismo límite diario y la misma reserva de la fase.

`runtime/news-scope-rechecks.json` conserva las solicitudes de reevaluación de
la auditoría. Los cierres automáticos afectados se reabren prioritariamente con
`news/recheck` y `review/pending`; los descartes históricos sin doble validación
también vuelven a pendientes. Una evaluación antigua no permite cerrarlos otra
vez. Los cierres y retiros humanos conservan prioridad.

Una inconsistencia conocida entre el título y el cuerpo queda vinculada a su
hash y no se vuelve a enviar a Gemini mientras persista. Si el medio corrige
el contenido, puede reintentarse con el texto nuevo. Los campos faltantes de
una noticia válida no se confunden con una fuente incoherente.

`data/selection/scope_audits.json` conserva exclusiones tras comprobar una fuente
que el clasificador interpretó incorrectamente. Son decisiones automáticas, con
`human_reviewed: false`, identidad bibliográfica, fecha, motivo acotado y hash
del texto; nunca contienen fichas, citas ni identidades extraídas. Una corrección
anterior conserva su historial, pero no sustituye la doble validación actual
ni bloquea una nueva evaluación positiva bajo la política inclusiva. Un texto
nuevo requiere otra evaluación y las decisiones humanas conservan prioridad.

## Interpretar los recuentos

El manifiesto v2 conserva `assessment_counts`. `evaluated` es la suma de
`relevant`, `excluded` y `uncertain`; `pending = candidates - evaluated`.
Las noticias sin evaluación nunca se suman a descartes. Las negativas antiguas
o sin segunda validación concordante son inciertas. Las decisiones humanas prevalecen.

El archivo público expone esos recuentos en `policy.selection_gate`, con la fecha
del snapshot `evaluated_at`. `selected` cuenta lo disponible tras decisiones
editoriales, que pueden ser posteriores al snapshot de análisis. La recolección
añade nuevas referencias sólo a pendientes; reducir la base exige conciliación.
Los recuentos describen el archivo procesado, no la cantidad total de noticias
de derechos humanos que existen en El Salvador.
