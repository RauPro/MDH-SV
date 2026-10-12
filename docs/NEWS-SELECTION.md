# Selección temática de noticias

El monitor publica posibles afectaciones de derechos humanos y contexto
sustantivo de su ejercicio, protección, defensa, reparación o memoria histórica,
con vínculo con El Salvador. Incluye leyes, políticas, investigaciones y trabajo
de defensores; no exige una violación probada ni una víctima identificada. Delitos
comunes, deportes o política general no bastan por sí solos, ni palabras como
«captura», «prisión» o «periodista».

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

La política `mdh-news-scope-3-rights-nexus` clasifica directamente
cualquier referencia tras leer su fuente: no exige ficha, issue ni etiqueta
positiva anterior. Incluye hacinamiento y reinserción como contexto cuando el
artículo trata sustantivamente esas cuestiones, sin convertir una política
pública en una denuncia de violación. Las fichas nuevas usan
`mdh-universal-news-grounded-10-rights-nexus`. La evaluación de alcance v3
prevalece sobre la clasificación auxiliar de una ficha del mismo texto.
Una condena, una investigación por corrupción o la justicia ordinaria por sí
solas no acreditan pertinencia: hace falta una afectación, riesgo o protección
concreta de derechos. El vínculo con El Salvador debe corresponder a ese mismo
asunto, no a una mención incidental separada.

El job horario clasifica primero y completa fichas después, bajo una única
reserva horaria. Asigna aproximadamente el 70% de las solicitudes a clasificación
(máximo ocho); el resto, incluidos recursos no usados por esa fase, a fichas.
Con diez solicitudes disponibles, el reparto inicial es siete y tres. Cada
solicitud clasifica hasta ocho artículos, reduciendo el lote según el tamaño
del JSON completo. Se preserva un intento para RSS nuevo antes del backlog
positivo. Los reintentos también consumen presupuesto.

Las decisiones quedan en `scope-reviews/`; v2 conserva un recibo de lectura con
fecha, método, truncamiento y hash, sin cuerpo. Las evaluaciones con políticas v1/v2 se reanalizan una vez antes de continuar
con referencias nuevas, sin reiniciar el contador de uso. Una ficha
puede conservar otro snapshot: no bloquea la clasificación de una lectura nueva
ni convierte los datos antiguos en campos aprobados.

Las cachés se cargan una vez por ejecución, sin miles de consultas individuales
a GitHub. Las fuentes inaccesibles tienen reintentos diferidos: 24 horas para
fallos ordinarios y siete días para acceso restringido, sin eludirlo. Los hashes
describen lo consultado; una caché no demuestra que la página no haya cambiado.

`review-scope.yml` queda para pruebas manuales; no duplica automáticamente el
presupuesto del job horario. Todos comparten el contador persistente de Gemini y
la pausa global ante 429. No se eleva el tope diario ni se habilita facturación.

## Interpretar los recuentos

El manifiesto v2 conserva `assessment_counts`. `evaluated` es la suma de
`relevant`, `excluded` y `uncertain`; `pending = candidates - evaluated`.
Las noticias sin evaluación nunca se suman a descartes. Las negativas antiguas
pendientes de reevaluar son inciertas. Las decisiones humanas prevalecen.

El archivo público expone esos recuentos en `policy.selection_gate`, con la fecha
del snapshot `evaluated_at`. `selected` cuenta lo disponible tras decisiones
editoriales, que pueden ser posteriores al snapshot de análisis. La recolección
añade nuevas referencias sólo a pendientes; reducir la base exige conciliación.
Los recuentos describen el archivo procesado, no la cantidad total de noticias
de derechos humanos que existen en El Salvador.
