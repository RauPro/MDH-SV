# Archivo bibliográfico histórico

El archivo comienza el **26 de marzo de 2025**, la fecha de incorporación del paper
a IEEE Xplore, y termina por defecto en la fecha de El Salvador al ejecutarse.
El 27 de noviembre de 2024 es la fecha bibliográfica y el inicio del congreso;
no es la fecha de incorporación a Xplore. La procedencia está en
`data/history/paper.json` y en la [página de IEEE](https://ieeexplore.ieee.org/document/10933836).

## Ejecución

```sh
python -m mdh_pipeline.history --start 2025-03-26 --end 2026-10-09 --max-pages 350
python -m mdh_pipeline.history --max-pages 150
python -m mdh_pipeline.history --export-only
pytest -q tests/test_history.py
```

El primer comando reproduce el intervalo inicial. El segundo amplía hasta hoy y
reanuda meses incompletos; vuelve a recorrer los dos meses más recientes para
capturar noticias añadidas o corregidas. `--refresh` fuerza un nuevo recorrido de
todos los meses, útil al cambiar el filtro de términos. Los cortes se escriben
atómicamente después de cada página, por medio, en `data/history/`.

Se consultan solamente los campos `id,date,link,title` de las APIs WordPress
públicas de Diario Co Latino, GatoEncerrado y Revista Factum. La paginación es
ascendente, con intervalos mensuales inclusivos, 100 entradas por petición, pausas,
reintentos limitados, TLS verificado y control de redirecciones, DNS y tamaño de
respuesta. El presupuesto limita páginas por medio y ejecución; no limita a un
número arbitrario de candidatos por mes. Los errores preservan lo ya obtenido y
quedan visibles en la cobertura. No se crea ninguna cuenta en los medios.

## Interpretación y límites

`apps/web/public/archive/news.json` es un índice de referencias, **no una colección
de casos acreditados**. Todas tienen `review_status: not_reviewed`,
`jurisdiction_status: not_reviewed` y `record_type: news_reference`. Sus temas son
el resultado de un filtro automático de títulos y URLs; pueden incluir asuntos de
otros países, opinión y noticias sin una violación. Las fechas son las declaradas
por el medio y no se sustituyen con la fecha actual cuando faltan.

Se guardan enlaces y títulos abreviados a 24 palabras/180 caracteres; no cuerpos,
imágenes, testimonios privados ni identidades extraídas. Los contenidos originales
conservan los derechos de sus medios. El API no aporta corroboración independiente.

La cobertura publica el número de páginas, entradas examinadas, referencias
seleccionadas, registros inválidos y fallos por mes y medio. `enumerated` significa
que se recorrieron las páginas que la API devolvió para ese intervalo. **Nunca
significa cobertura completa de prensa, DD. HH. o de los archivos de un medio**.
La cobertura global se identifica siempre como parcial. No se deduplican textos
sindicados entre medios; cada fuente conserva su referencia.

Una noticia puede relacionarse con una ficha existente por URL. Crear una ficha
estructurada de hechos, personas afectadas, ubicación y presuntos responsables
requiere revisar la fuente y sus riesgos. Este colector no modifica
`data/reviewed`, no crea publicaciones, y no dispone de la cola editorial privada.
La cola periódica de RSS mantiene su proceso independiente de aprobación humana.

`python -m mdh_pipeline.history_review --limit 20` permite transferir las referencias
más antiguas a esa misma cola privada en lotes acotados. Requiere `GH_TOKEN` y
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
La publicación del sitio debe comprobarse con la integración de Vercel o mediante
un mecanismo de despliegue explícito; aprobar una noticia en la cola privada no se
convierte en una publicación por esta programación.
