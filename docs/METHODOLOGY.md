# Metodología de MDH-SV

Versión inicial · 9 de octubre de 2026.

## Objeto y unidad de registro

El monitor indexa reportes públicos de presuntas violaciones de derechos humanos en El Salvador para consulta institucional y ciudadana. Una ficha no es una conclusión jurídica ni una determinación independiente sobre una persona.

- `incident`: un caso individual descrito por una fuente. No implica una persona única en todo el conjunto.
- `aggregate`: un reporte colectivo o conjunto de eventos. No se descompone en casos inventados.
- `publication`: un documento de contexto. No se cuenta como incidente.

La cobertura inicial es deliberadamente pequeña y no representativa: 17 reportes individuales, dos colectivos y cinco informes de contexto, respaldados por 16 documentos, incluidas actualizaciones. No se pueden derivar tendencias nacionales, tasas de incidencia, totales de víctimas ni comparaciones departamentales representativas. La ausencia de registros no prueba ausencia de violaciones.

El archivo bibliográfico tiene un alcance distinto al de esas fichas: busca recuperar noticias desde el 1 de enero de 2010 hasta la fecha local de cada ejecución. El intervalo publicado en `coverage.start_date` y `coverage.end_date` indica el periodo solicitado, no una cobertura completa. Los meses pendientes, recorridos o con fallos se muestran por fuente. La incorporación del artículo original a IEEE Xplore en 2025 es un antecedente de la investigación, no el inicio de este archivo. Ampliar el intervalo no transforma referencias pendientes en casos acreditados.

## Procedencia y revisión

Cada fila contiene autor o institución, título de la fuente, URL HTTPS y localizador en el documento, junto con la fecha de consulta. Las fichas iniciales fueron elaboradas mediante consulta documental asistida por IA; **su revisión humana está pendiente**. `source_checked` solo significa que se consultó la fuente; nunca equivale a `confirmed`. No se atribuye apoyo institucional a las organizaciones citadas.

El original conserva las conclusiones, atribuciones, reservas y metodología de su autor. Los resúmenes del monitor son breves, atribuidos y no reemplazan ese documento. La clasificación temática facilita búsqueda y no adjudica responsabilidad. Los posibles responsables se atribuyen a la fuente y no se deducen por mera mención de una institución.

## Fechas y lugar

`published_date` corresponde al documento; `incident_date` al hecho expresamente fechado. Se utiliza `null` si no consta. Una fecha de notificación de fallecimiento no se convierte en fecha de muerte. `month_range` expresa meses inclusivos; el primer y último día son límites técnicos para filtrar, no fechas observadas. Los filtros de fechas se aplican al intervalo del hecho y excluyen fechas desconocidas.

Solo se publica departamento cuando está respaldado por la fuente. No se asigna automáticamente San Salvador a hechos nacionales. Todas las geometrías actuales son `null`, evitando presentar centroides como ubicaciones de víctimas.

## Protección de personas

Se omiten identidades de menores, edades exactas, domicilios, coordenadas individuales, fotografías, historias clínicas, expedientes privados e identidades inferidas. En casos ampliamente documentados pueden conservarse los nombres públicos de defensores o los seudónimos judiciales necesarios para identificar la fuente. No se agregan datos personales ajenos al objeto de documentación. Los documentos enlazados pueden contener datos personales bajo sus propias políticas. No se reconstruyen identidades a partir de seudónimos. El esquema público rechaza campos adicionales.

Una solicitud de retiro puede señalar solo el ID de la ficha en un issue público. No existe todavía un canal confidencial; nunca se debe pedir a una persona que revele circunstancias privadas para solicitar una corrección. El sitio no recibe denuncias ni atiende emergencias.

## Incorporación y corrección

1. Encontrar la fuente original y comprobar autoría, fecha, integridad y alcance. No basta una página de resultados ni un enlace a la portada de una ONG.
2. Identificar una unidad de registro localizable; mantener separados informes y casos.
3. Elaborar un resumen neutral atribuido, fechas fieles y localizador. No completar campos desconocidos con invenciones.
4. Revisar riesgos para personas, posible solapamiento con registros existentes y condiciones de reutilización. La coincidencia de centro penal, día o palabras no identifica a una víctima ni autoriza una fusión.
5. Antes de usar el conjunto para conclusiones institucionales, un revisor humano con competencia temática debe contrastar cada ficha. El estado `editorially_reviewed` exige `human_reviewed=true`, identificador del revisor, fecha y notas sustantivas. Las candidatas de la cola llevan `origin_candidate_id` y no pueden publicarse sin ese estado; una etiqueta manual en GitHub no sustituye la aprobación validada. La revisión editorial no certifica hechos ni culpabilidad.
6. Editar la fuente canónica, ejecutar `python -m mdh_pipeline.publication`, revisar todas las diferencias y correr las pruebas. Publicar un commit y conservar su identificador para reproducibilidad.
7. Ante un error o riesgo, retirar temporalmente la ficha del conjunto público, regenerar descargas y desplegar. Registrar una nota sin exponer datos sensibles. Si alguna vez se publica información personal indebidamente, retirar el archivo actual no borra copias o historial: evaluar purga de historial y cachés con el responsable y la persona afectada.

## IA, deduplicación y preservación

La recolección automática está separada de la publicación. Las noticias candidatas se conservan como issues en un repositorio privado. Solo se almacenan enlace, titular limitado y metadatos; nunca el cuerpo completo de la noticia. El workflow de recolección carece de permisos de escritura en el repositorio público. No se envía información a Gemini sin activar expresamente esa opción. La IA no aumenta el nivel de certeza; su salida requiere revisión.

La deduplicación automática solo evita reingestas idénticas. Dos medios pueden depender de una misma fuente, por lo que su número no demuestra independencia. La Wayback Machine es un respaldo de disponibilidad, no una garantía de inmutabilidad, veracidad o validez probatoria. No se fabrican URLs de archivo si una solicitud falla.

El manifest SHA-256 verifica los bytes de nuestras exportaciones. No representa un hash del artículo original ni una cadena de custodia forense.

## Licencias y correcciones

Código bajo MIT. Metadatos y resúmenes propios: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), con atribución a MDH-SV y versión consultada. Documentos originales: derechos de sus respectivos autores; no se distribuyen copias completas. Las correcciones no sensibles se reciben en [GitHub](https://github.com/RauPro/MDH-SV/issues). No hay plazo de respuesta garantizado.


## Fuentes complementarias y precisión temporal

`related_sources` conserva actualizaciones, como una liberación posterior, dentro del mismo expediente. No crea un segundo caso ni aumenta automáticamente la certeza. El conteo de documentos elimina URLs repetidas entre fuentes principales y complementarias. `year_range` expresa el año conocido; los límites del año se usan únicamente para filtrar y la interfaz muestra su precisión anual. No se presenta la fecha de una sentencia como fecha del hecho.

Las ampliaciones documentales realizadas durante la preparación inicial siguen como `source_checked`, con revisión humana pendiente. Las incorporaciones futuras procedentes de la recolección tienen una aprobación editorial explícita antes de preparar su publicación.

## Consulta territorial

En la colección Reportes documentados, el mapa interactivo muestra únicamente reportes individuales con departamento documentado y respeta la búsqueda, temática, fechas y filtro de custodia. Los cinco reportes individuales sin departamento identificado en la edición actual se consultan por separado; nunca se sitúan en un centroide. Los informes y registros colectivos no se suman a esos recuentos. Un valor cero describe el conjunto consultado, no la ausencia de violaciones. Los contornos departamentales son una cartografía simplificada de Simplemaps, con fuente y licencia visibles, y no representan ubicaciones personales.

En la colección Noticias, las referencias bibliográficas y sus fichas se consultan desde la vista principal, aunque los campos estén pendientes. Su mapa mantiene conteos propios y capas separadas: ubicaciones revisadas y departamentos extraídos automáticamente, marcados Sin revisar. La segunda capa exige jurisdicción salvadoreña en la extracción y mención departamental explícita en el párrafo citado; no aprueba la ficha ni confirma ubicación de una violación. Las decisiones humanas prevalecen sobre la capa automática. Las noticias sin esa ubicación permanecen listables; no se deduce el departamento del título, las etiquetas de búsqueda ni un municipio. Los filtros temporales de noticias usan la fecha de publicación. Una noticia ubicada no se convierte en un caso ni en una persona adicional.
