# Metodología de MDH-SV

Versión inicial · 9 de octubre de 2026.

## Objeto y unidad de registro

El monitor indexa reportes públicos de presuntas violaciones de derechos humanos en El Salvador para consulta institucional y ciudadana. Una ficha no es una conclusión jurídica ni una determinación independiente sobre una persona.

- `incident`: un caso individual descrito por una fuente. No implica una persona única en todo el conjunto.
- `aggregate`: un reporte colectivo o conjunto de eventos. No se descompone en casos inventados.
- `publication`: un documento de contexto. No se cuenta como incidente.

La cobertura inicial es deliberadamente pequeña y no representativa: cuatro reportes individuales, un colectivo y cuatro informes, respaldados por cinco documentos. No se pueden derivar tendencias nacionales, tasas de incidencia, totales de víctimas ni comparaciones departamentales representativas. La ausencia de registros no prueba ausencia de violaciones.

## Procedencia y revisión

Cada fila contiene autor o institución, título de la fuente, URL HTTPS y localizador en el documento, junto con la fecha de consulta. Las fichas iniciales fueron elaboradas mediante consulta documental asistida por IA; **su revisión humana está pendiente**. `source_checked` solo significa que se consultó la fuente; nunca equivale a `confirmed`. No se atribuye apoyo institucional a las organizaciones citadas.

El original conserva las conclusiones, atribuciones, reservas y metodología de su autor. Los resúmenes del monitor son breves, atribuidos y no reemplazan ese documento. La clasificación temática facilita búsqueda y no adjudica responsabilidad. Los posibles responsables se atribuyen a la fuente y no se deducen por mera mención de una institución.

## Fechas y lugar

`published_date` corresponde al documento; `incident_date` al hecho expresamente fechado. Se utiliza `null` si no consta. Una fecha de notificación de fallecimiento no se convierte en fecha de muerte. `month_range` expresa meses inclusivos; el primer y último día son límites técnicos para filtrar, no fechas observadas. Los filtros de fechas se aplican al intervalo del hecho y excluyen fechas desconocidas.

Solo se publica departamento cuando está respaldado por la fuente. No se asigna automáticamente San Salvador a hechos nacionales. Todas las geometrías actuales son `null`, evitando presentar centroides como ubicaciones de víctimas.

## Protección de personas

No se incorporan nombres de víctimas, edades exactas, domicilios, coordenadas individuales, fotografías, historias clínicas, expedientes privados ni identidades inferidas. Los documentos enlazados pueden contener datos personales bajo sus propias políticas. No se reconstruyen identidades a partir de seudónimos. El esquema público rechaza campos adicionales.

Una solicitud de retiro puede señalar solo el ID de la ficha en un issue público. No existe todavía un canal confidencial; nunca se debe pedir a una persona que revele circunstancias privadas para solicitar una corrección. El sitio no recibe denuncias ni atiende emergencias.

## Incorporación y corrección

1. Encontrar la fuente original y comprobar autoría, fecha, integridad y alcance. No basta una página de resultados ni un enlace a la portada de una ONG.
2. Identificar una unidad de registro localizable; mantener separados informes y casos.
3. Elaborar un resumen neutral atribuido, fechas fieles y localizador. No completar campos desconocidos con invenciones.
4. Revisar riesgos para personas, posible solapamiento con registros existentes y condiciones de reutilización. La coincidencia de centro penal, día o palabras no identifica a una víctima ni autoriza una fusión.
5. Antes de usar el conjunto para conclusiones institucionales, un revisor humano con competencia temática debe contrastar cada ficha. Cualquier futura certificación humana requiere ampliar explícitamente el esquema y registrar revisor, fecha, evidencia y alcance; no basta cambiar una etiqueta.
6. Editar la fuente canónica, ejecutar `python -m mdh_pipeline.publication`, revisar todas las diferencias y correr las pruebas. Publicar un commit y conservar su identificador para reproducibilidad.
7. Ante un error o riesgo, retirar temporalmente la ficha del conjunto público, regenerar descargas y desplegar. Registrar una nota sin exponer datos sensibles. Si alguna vez se publica información personal indebidamente, retirar el archivo actual no borra copias o historial: evaluar purga de historial y cachés con el responsable y la persona afectada.

## IA, deduplicación y preservación

La recolección automática está separada de la publicación. Los candidatos se guardan localmente y no se suben como artefactos de GitHub. No se envía información a Gemini sin activar expresamente esa opción. La IA no aumenta el nivel de certeza; su salida requiere revisión.

La deduplicación automática solo evita reingestas idénticas. Dos medios pueden depender de una misma fuente, por lo que su número no demuestra independencia. La Wayback Machine es un respaldo de disponibilidad, no una garantía de inmutabilidad, veracidad o validez probatoria. No se fabrican URLs de archivo si una solicitud falla.

El manifest SHA-256 verifica los bytes de nuestras exportaciones. No representa un hash del artículo original ni una cadena de custodia forense.

## Licencias y correcciones

Código bajo MIT. Metadatos y resúmenes propios: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), con atribución a MDH-SV y versión consultada. Documentos originales: derechos de sus respectivos autores; no se distribuyen copias completas. Las correcciones no sensibles se reciben en [GitHub](https://github.com/RauPro/MDH-SV/issues). No hay plazo de respuesta garantizado.
