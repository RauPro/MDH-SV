# Archivo bibliográfico histórico

El periodo objetivo del archivo comienza el **1 de enero de 2010** y termina por
defecto en la fecha de El Salvador al ejecutarse. Es un alcance solicitado, no una
afirmación de que todos los meses estén recorridos ni de que los medios conserven
publicaciones de todo ese periodo. La cobertura efectiva sigue siendo parcial y
se declara por medio y mes. La interfaz toma el intervalo publicado de
`coverage.start_date` y `coverage.end_date`, sin fijarlo a la fecha del paper.

El artículo de investigación es un antecedente del proyecto, no el inicio del
archivo. Se incorporó a IEEE Xplore el 26 de marzo de 2025; el 27 de noviembre de
2024 corresponde a la fecha bibliográfica y al inicio del congreso. La procedencia está en
`data/history/paper.json` y en la [página de IEEE](https://ieeexplore.ieee.org/document/10933836).

## Ejecución

```sh
python -m mdh_pipeline.history --start 2010-01-01 --max-pages 150
python -m mdh_pipeline.history --max-pages 150
python -m mdh_pipeline.history --export-only
pytest -q tests/test_history.py
```

El primer comando fija explícitamente 2010 como inicio y omite `--end` para usar
el día actual en El Salvador. El segundo utiliza ese mismo inicio por defecto y
reanuda meses incompletos; vuelve a recorrer los dos meses más recientes para
capturar noticias añadidas o corregidas. `--refresh` fuerza un nuevo recorrido de
todos los meses, útil al cambiar el filtro de términos. Los cortes se escriben
atómicamente después de cada página, por medio, en `data/history/`.

Se consultan solamente los campos `id,date,link,title` de las APIs WordPress
públicas de Diario Co Latino, GatoEncerrado y Revista Factum. La paginación es
ascendente, con intervalos mensuales inclusivos, 100 entradas por petición, pausas,
reintentos limitados, TLS verificado y control de redirecciones, DNS y tamaño de
respuesta. `--max-pages` limita intentos de página por medio y ejecución, incluidos
los fallidos; no limita a un número arbitrario de candidatos por mes. Cada intento
de página admite hasta dos reintentos HTTP internos, de modo que ese presupuesto
no equivale a un límite de peticiones HTTP individuales. Los errores preservan lo ya obtenido y
quedan visibles en la cobertura. El presupuesto puede agotarse antes de recorrer
todos los años; las ejecuciones siguientes continúan desde los checkpoints.
No se crea ninguna cuenta en los medios.

Con el presupuesto habitual de 150 intentos y el histórico pendiente, se reservan
hasta 20 para los dos meses más recientes; el resto queda disponible para avanzar
en los años anteriores. El cupo reciente no utilizado también puede aprovecharse
en el histórico. Así, recuperar el periodo desde 2010 no paraliza las noticias
nuevas. Un presupuesto de un solo intento atiende el histórico; cuando este ya
está recorrido, el presupuesto se dedica a los meses recientes.

Tres fallos consecutivos detienen ese medio durante la ejecución; un éxito
reinicia el contador. Los meses históricos no intentados o parciales se recorren
primero, por fecha. Los fallidos se reintentan después, comenzando por el intento
más antiguo y usando la fecha del mes como desempate, para que un fallo persistente
no bloquee todos los años posteriores. Los fallos siguen visibles y se reintentan
en ejecuciones posteriores.

El resumen por fuente distingue `requests` (intentos de página),
`successful_pages`, `failed_page_attempts` y
`stopped_after_consecutive_failures`. Estos contadores describen la ejecución,
no acreditan cobertura completa ni validación editorial.

## Interpretación y límites

`apps/web/public/archive/news.json` es un índice de referencias, **no una colección
de casos acreditados**. Todas tienen `review_status: not_reviewed`,
`jurisdiction_status: not_reviewed` y `record_type: news_reference`. Sus temas son
el resultado de un filtro automático de títulos y URLs; pueden incluir asuntos de
otros países, opinión y noticias sin una violación. Las fechas son las declaradas
por el medio y no se sustituyen con la fecha actual cuando faltan.

`scope_hint` distingue `el_salvador`, `international_context` y `undetermined` como
**indicios para explorar el archivo**, sin modificar `jurisdiction_status`.
`scope_terms` conserva los fragmentos explícitos detectados en título y URL,
normalizados sin tildes, en minúsculas y con separadores de URL como espacios.
Referencias a El Salvador, salvadoreños, Bukele, CECOT o nombres inequívocos como
Ruth López y Fidel Zavala tienen prioridad sobre referencias internacionales. PNC,
«régimen de excepción», «San Miguel» o «Rubén Zamora» por sí solos no prueban relación
salvadoreña. Si faltan términos precisos, la referencia permanece sin determinar;
**eso no significa que carezca de relación con El Salvador**.

Las nuevas capturas calculan este indicio antes de abreviar el título. La migración
del archivo inicial usa sus títulos ya guardados y URLs completas, sin nuevas
peticiones, y preserva fechas, contadores de cobertura y estados de revisión.
La clasificación puede fallar: la pertinencia debe resolverse durante la revisión.

Se guardan enlaces y títulos abreviados a 24 palabras/180 caracteres; no cuerpos,
imágenes, testimonios privados ni identidades extraídas. Los contenidos originales
conservan los derechos de sus medios. El API no aporta corroboración independiente.

La cobertura publica el número de páginas, entradas examinadas, referencias
seleccionadas, registros inválidos y fallos por mes y medio. `enumerated` significa
que se recorrieron las páginas que la API devolvió para ese intervalo. **Nunca
significa cobertura completa de prensa, DD. HH. o de los archivos de un medio**.
La cobertura global se identifica siempre como parcial. No se deduplican textos
sindicados entre medios; cada fuente conserva su referencia.

Un medio puede devolver publicaciones sólo a partir de una fecha posterior a 2010.
Ese límite describe lo accesible mediante su API en la consulta realizada, no el
inicio del medio ni la inexistencia de noticias anteriores. Los meses recorridos
sin referencias no demuestran ausencia de hechos ni un archivo completo.

Una noticia puede relacionarse con una ficha existente por URL. Crear una ficha
estructurada de hechos, personas afectadas, ubicación y presuntos responsables
requiere revisar la fuente y sus riesgos. Este colector no modifica
`data/reviewed`, no crea publicaciones, y no dispone de la cola editorial privada.
La cola periódica de RSS mantiene su proceso independiente de aprobación humana.

`python -m mdh_pipeline.history_review --limit 20` permite transferir referencias
a esa misma cola privada en lotes acotados. Prioriza indicios de El Salvador y luego
las referencias sin determinar; dentro de cada grupo procesa las más antiguas.
Excluye el contexto internacional por defecto. Solo el operador puede incluirlo
expresamente con `--include-international`, y se atiende después de los otros grupos.
Los contadores `eligible_references`, `remaining` y `already_in_review_queue` se
refieren al conjunto elegible; `excluded_international` explica la exclusión.
Requiere `GH_TOKEN` y
`GITHUB_REPOSITORY` del repositorio privado: `GitHubInbox` rechaza repositorios
públicos. La deduplicación usa el mismo identificador de URL de la recolección RSS,
incluidas noticias rechazadas o ya aprobadas. No cambia el estado público de una
referencia ni publica una ficha. Valida toda la entrada antes de crear un issue.
`review-history.yml` es una plantilla para el repositorio **privado**, con el SHA
de código revisado en `MDH_PUBLIC_CODE_REF`; el archivo bibliográfico se lee de
`main` para incorporar futuras referencias. No se debe instalar en el repositorio
público. Permite veinte referencias cada seis horas y comparte el grupo de
concurrencia de la cola RSS para evitar carreras de deduplicación.

## Programación

`refresh-history.yml` es la plantilla para `.github/workflows/refresh-history.yml`:
se ejecuta cada seis horas y permite ejecución manual. Solo añade los tres
checkpoints y el JSON bibliográfico a un commit. No usa `git add .` ni tiene
credenciales del repositorio privado. El usuario autorizó la recolección periódica
y el despliegue; el mantenedor integra esta plantilla después de revisar las pruebas.

Los commits hechos con `GITHUB_TOKEN` no disparan otros workflows de GitHub Actions.
El paso de recolección tiene un máximo de veinte minutos. Si falla o agota ese
tiempo, el workflow vuelve a exportar los checkpoints disponibles, los valida y
guarda el progreso antes de marcar la ejecución como fallida. Un error de
validación impide el commit. Esto permite reanudar el recorrido sin ocultar el fallo.
La publicación del sitio debe comprobarse con la integración de Vercel o mediante
un mecanismo de despliegue explícito; aprobar una noticia en la cola privada no se
convierte en una publicación por esta programación.
