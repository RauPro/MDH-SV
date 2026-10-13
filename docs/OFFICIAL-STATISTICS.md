# Estadísticas oficiales de homicidios

Esta capa reúne agregados publicados por el Instituto de Medicina Legal (IML), la Fiscalía General de la República (FGR) y la Oficina de las Naciones Unidas contra la Droga y el Delito (UNODC). Sirve para consultar el contexto estadístico de El Salvador por año y departamento. Está separada de las noticias, las fichas y los casos de derechos humanos: una cifra de homicidios no es un conteo de noticias ni identifica por sí misma violaciones de derechos humanos.

El archivo canónico es [`data/official-statistics/homicides.json`](../data/official-statistics/homicides.json). La primera edición tiene corte al **12 de octubre de 2026, hora de El Salvador**. Las horas de consulta y generación se guardan en UTC; por eso pueden corresponder al 13 de octubre sin cambiar el corte local.

## Cobertura y ausencias

| Período | Fuente | Nacional | Departamentos | Meses importados |
| --- | --- | --- | --- | --- |
| 2010–2011 | Anuarios IML | Disponible | Los 14 | No |
| 2012 | Base UNODC, edición julio de 2026 | Disponible | No disponibles | No |
| 2013–2021 | Anuarios IML | Disponible | Los 14 | No |
| 2022–2025 | Tabla HTML FGR | Disponible | No publicados en esa fuente | Los 12 |
| 2026 | Tabla HTML FGR | Parcial: enero–agosto | No publicados en esa fuente | Enero–agosto |

El archivo contiene una entrada para cada año de 2010 a 2026 y exactamente 14 departamentos en cada entrada. En esta edición hay 17 agregados nacionales disponibles, incluido el parcial de 2026, y 154 valores departamentales disponibles, correspondientes a once años. Las ausencias son `null` con estado `not_available`; nunca se convierten en cero. Para los meses futuros respecto del corte se utiliza `outside_cutoff`.

En 2026, `start_date` es `2026-01-01` y `end_date` es `2026-08-31`, porque sólo ese intervalo tiene valores publicados. Septiembre y octubre figuran como no disponibles. Noviembre y diciembre están fuera del corte. No se extrapola el total de 49 a todo el año ni se presenta como cierre anual.

## Fuentes y trazabilidad

Cada período referencia una fuente por `source_id`. Cada fuente registra institución, título, URL oficial, formato, fecha de consulta, SHA-256 del documento descargado o del HTML recibido y una nota de edición. Cada cifra disponible conserva un localizador de página o de tabla, fila y columna. El hash identifica los bytes consultados; no prueba por sí solo la exactitud de una estadística oficial.

Las descargas IML proceden del [catálogo de estadísticas del Órgano Judicial](https://transparencia.oj.gob.sv/es/documentos/248). Los datos recientes proceden de [Estadísticas de homicidios de FGR](https://www.fiscalia.gob.sv/estadisticas-de-homicidios/), que identifica como fuente al Equipo Operativo de Homicidios y no muestra una fecha de actualización. Las URL individuales, los hashes y los localizadores están en el JSON.

Para 2012 se utiliza el [archivo original de UNODC, edición julio de 2026](https://data.unodc.org/sites/dataportal.unodc.org/files/2026-07/data_cts_intentional_homicide.xlsx): hoja `data_cts_intentional_homicide`, fila 3321, columnas A:M. La fila identifica El Salvador, víctimas de homicidio intencional, todas las categorías en `Total`, año 2012, unidad `Counts`, valor 2,594 y fuente `OAS/CTS/SDG`. Se comprobó que es la única fila que cumple esos filtros en esa hoja. No se utiliza la hoja de estimaciones regionales ni se suman desagregaciones al total.

Los anuarios 2010–2014 atribuyen sus bases al cotejo o consenso entre IML, FGR y PNC. El de 2021 identifica expresamente a la Mesa tripartita. Los anuarios 2015–2020 utilizados se atribuyen al IML; no se añade una atribución directa a PNC que no figure en el documento. La fecha de actualización del portal no se confunde con el año de los hechos ni con una fecha original de publicación.

La carga inicial se preparó mediante lectura de las tablas, inspección visual de cada tabla departamental utilizada, una transcripción de comprobación independiente de la extracción textual y verificación de sumas. Es una comprobación de transcripción y consistencia, no una certificación independiente de los registros del Estado. No se incorporaron nombres, expedientes, direcciones, microdatos ni coordenadas. Los PDF originales no se versionan en el repositorio público.

## Unidades y comparabilidad

En IML se utiliza `unit: "persons"`: los cuadros cuentan personas fallecidas o reconocimientos de víctimas según su universo publicado. No se transforman en número de expedientes o de incidentes únicos. UNODC 2012 también utiliza `persons`, con indicador `intentional_homicides` porque la fila cuenta víctimas de homicidio intencional. En FGR se conserva `unit: "source_reported_homicides"` porque la tabla no ofrece una definición suficiente para convertirla a otro tipo de unidad. El rótulo de 2022 es «Total de Homicidios» (`indicator: "homicides"`); desde 2023 es «Total Homicidios Intencionales» (`indicator: "intentional_homicides"`).

La [guía metodológica del Ministerio de Justicia y Seguridad Pública de 2021](https://www.seguridad.gob.sv/wp-content/uploads/download-manager-files/Gu%C3%ADa%20sobre%20estad%C3%ADsticas%20de%20homicidios%20y%20feminicidios%20en%20El%20Salvador%20mayo%202021.pdf), páginas 2–6 del PDF, explica la conciliación interinstitucional y las revisiones posteriores. Distingue agresión, muerte y levantamiento del cadáver; sus fechas pueden diferir. También describe el tratamiento de hallazgos antiguos y la diferenciación jurídica de feminicidios. Por ello no se equipara una víctima mujer a un feminicidio ni se presupone que todos los años sean metodológicamente homogéneos.

Las tablas territoriales IML se refieren al lugar de agresión u ocurrencia. No se reinterpretan como domicilio de las víctimas. Esta versión sólo publica departamentos: no reconstruye municipios a partir de noticias, rankings, residencias o nombres parecidos. Los rankings de 35 municipios de 2010–2011 tampoco autorizan a rellenar otros municipios con ceros.

## Decisiones de esta edición

- **2012 utiliza UNODC para el total nacional y deja los departamentos no disponibles.** El total de 2,594 procede de otra institución y edición; no se reconstruye sumando categorías del IML. El PDF IML informa 2,246 hombres en la página 1 y 321 mujeres en la página 8, pero no presenta un total general consolidado ni explica el tratamiento de sexo indeterminado. La distribución departamental de mujeres no aparece. Los 281 menores de la página 10 son un subconjunto, no una tercera categoría sumable. No se reutiliza esa distribución territorial ni se reparte el total UNODC entre departamentos.
- **2018 conserva nacional y departamentos, sin importar meses.** En la página 1, septiembre tiene 173 hombres, 23 mujeres y cero indeterminados, pero el total impreso es 296. Las filas de esa columna suman 3,446 frente al total anual de 3,346. Los 14 departamentos de la página 8 sí suman 3,346. No se modifica silenciosamente una celda de la fuente.
- **2021 utiliza el universo de 1,147.** La página 3 separa 1,147 homicidios y 68 homicidios/osamentas, para 1,215 víctimas totales. El desglose departamental de la página 7 corresponde a 1,147. Los 68 hallazgos se documentan en una nota, sin sumarlos ni distribuirlos entre departamentos.
- **2022 mantiene la versión FGR de 496.** El [balance ministerial publicado el 4 de enero de 2023](https://www.seguridad.gob.sv/gabinete-de-seguridad-presenta-balance-con-datos-historicos-en-materia-de-combate-a-la-criminalidad-en-el-pais/) informó 495. Se conserva la divergencia y la procedencia; no se promedian ni se fusionan ambas versiones.
- **2022–2026 no tienen distribución territorial en la fuente elegida.** No se reparte el total nacional entre departamentos ni se usa la ubicación mencionada en una noticia para completar esa ausencia.

## Contrato y actualización

`schema_version: 1` identifica el contrato de esta capa. `completeness` describe el período: `complete`, `partial` o `unavailable`. `months: null` significa que esa edición no importa una serie mensual; es distinto de un arreglo de doce meses con valores faltantes explícitos. Toda cifra disponible es un entero no negativo con localizador; todo dato no disponible tiene valor y localizador nulos.

Para actualizar la capa se debe consultar nuevamente el original, registrar su hash y fecha, comprobar la etiqueta del período y su unidad, revisar visualmente las tablas e incorporar sólo valores respaldados. Las sumas departamentales completas deben coincidir con el agregado nacional del mismo universo y edición. Las sumas mensuales disponibles deben coincidir con el total del intervalo publicado. Si aparecen revisiones, discrepancias o una nueva metodología, deben explicarse antes de sustituir cifras.

La actualización de fuentes no implica aprobación de fichas de noticias ni modifica su revisión humana. Las cifras oficiales tampoco atribuyen responsabilidad penal o institucional a personas concretas.

## Publicación y reutilización

La pestaña **Estadísticas oficiales** (`/#estadisticas`) carga un snapshot validado, con filtros por año y departamento. La consulta de esta edición no consume Gemini ni depende de la cola de noticias. La actualización de estas cifras es documental y revisada; el cron de noticias no refresca esta colección automáticamente.

Después de incorporar y comprobar una nueva edición en el archivo canónico:

```sh
node apps/web/scripts/export-official-statistics.mjs
node apps/web/scripts/export-official-statistics.mjs --check
node --test tests/official-statistics-parser.test.mjs tests/official-statistics-export.test.mjs
.venv/bin/pytest -q tests/test_official_statistics_data.py
npm --prefix apps/web run build
```

Se versionan conjuntamente el canónico, sus notas metodológicas y las tres descargas en `apps/web/public/statistics/`. El build compara todos los bytes de las descargas con su exportación determinista; cualquier diferencia o archivo adicional detiene el despliegue. `manifest.json` contiene el SHA-256 del canónico y los hashes y tamaños de las descargas.

- `/statistics/homicides.json`: conjunto completo, incluidas las observaciones mensuales disponibles.
- `/statistics/homicides.csv`: una fila nacional y catorce departamentales por período, con fuente, unidad, localizador y estado. Los datos ausentes quedan vacíos; cero sigue siendo cero. `territory_level` distingue ámbitos solapados, que no deben sumarse. Los meses se consultan en JSON. Los textos con prefijos de fórmula reciben un apóstrofo para abrirlos de forma segura en una hoja de cálculo.
- `/api/v1/official-statistics`: colección de sólo lectura con filtros `year` y `department` (`national` o nombre canónico del departamento).
- MCP `get_official_statistics`: la misma colección y filtros para otros proyectos. Las cifras oficiales no alteran `/api/v1/stats`, que sigue describiendo los registros documentales del monitor.
