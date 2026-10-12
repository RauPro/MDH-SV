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

Clasificaciones `not_relevant`, `other` o `unclear`, evidencia ausente y
contradicciones quedan fuera. Un fallo de validación no vuelve a mostrar todos
los candidatos. Los campos faltantes no impiden por sí solos seleccionar una
noticia: pertinencia, privacidad, ficha completa y validación de un caso son
controles diferentes.

## Reevaluación y cuota

`mdh-universal-news-grounded-8-relevance-scope` se aplica a nuevas extracciones.
Los positivos antiguos necesitan la reevaluación `mdh-news-scope-1`, vinculada al
mismo artículo y hash; no se promociona una etiqueta cambiándole la versión.

El workflow privado `Reevaluar pertinencia de noticias` relee artículos y agrupa
hasta ocho por solicitud, con límites de texto y tiempo. Guarda sólo decisiones
y procedencia en `scope-reviews/`, sin regenerar fichas. Se ejecuta después de la
extracción y admite inicio manual. Por defecto usa como máximo dos solicitudes.
Comparte el contador persistente de Gemini: cada intento consume presupuesto y
un 429 activa la pausa común. No modifica el tope diario ni habilita facturación.
Los límites del operador no garantizan evitar límites no publicados por Google.

Las reevaluaciones válidas se reutilizan. Si el contenido cambió desde la
extracción, la noticia queda pendiente. Una nueva versión del criterio requiere
migración explícita; versiones desconocidas no se admiten automáticamente.
El publicador aplica decisiones nuevas y humanas, actualiza la selección y
retira las ubicaciones de noticias que ya no pertenecen a ella.
