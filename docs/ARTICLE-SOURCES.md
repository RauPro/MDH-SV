# Lectura de fuentes para borradores privados

`mdh_pipeline.article_source.fetch_article(candidate)` recupera texto público de
los medios habilitados en `publishers.py` para preparar un borrador que deberá
revisarse. Diario Co Latino, GatoEncerrado y Revista Factum conservan su ruta
WordPress; El Faro, Diario El Mundo y el archivo de El Diario de Hoy usan HTML
público. La Prensa Gráfica figura en el registro, pero su estado restringido impide
consultar artículos automáticamente. No llama a Gemini, no publica fichas y no
guarda cuerpos en disco, en GitHub ni en el archivo bibliográfico público.

La entrada es una referencia existente de la cola o del archivo: `source_url`,
`publisher`, `source_title` o `title`, y `published_date`. Sólo para fuentes con
`kind: wordpress`, si existe
`discovery.source_post_id`, se consulta ese identificador en la API WordPress
configurada para el medio; **nunca se usa un endpoint recibido de la noticia**.
En otro caso se intenta una sola consulta acotada por slug. En ambos caminos el
enlace declarado por WordPress debe coincidir con la noticia original; sólo se
admite la variante `www` configurada y la barra final.

Si esa API pública no existe o no aporta texto utilizable, se intenta el HTML
público de la misma noticia, dentro de un contenedor semántico de artículo. Se
eliminan navegación, scripts, estilos, formularios, anuncios, contenidos relacionados
y controles de compartir. Se preservan párrafos, encabezados y elementos de lista
como unidades numeradas `p1`, `p2`, etc. Estos identificadores corresponden a la
versión normalizada recuperada, no a la numeración editorial del medio. Las fuentes
`kind: html_archive` consultan directamente la URL del artículo: no prueban una
API WordPress ni usan un endpoint proporcionado en sus metadatos de descubrimiento.

## HTML público de los nuevos medios

Perfiles comprobados el 10 de octubre de 2026; no constituyen una garantía de que
todas las plantillas históricas del medio sean iguales:

| Medio | Contenedor editorial comprobado | Fecha del artículo |
| --- | --- | --- |
| El Faro (`beta.elfaro.net`) | `.article #main-text` | `article:published_time`, admitiendo el separador de espacio del ISO publicado por el medio. |
| Diario El Mundo (`diario.elmundo.sv`) | `article .article-body` | `article:published_time` o `datePublished` del JSON-LD correspondiente a esa URL. |
| El Diario de Hoy, archivo (`historico.elsalvador.com`) | `article.detail .entry-content` | `article:published_time`; no se sustituye por `dateModified` ni por el `lastmod` del sitemap. |
| La Prensa Gráfica | Sin selector confirmado: extracción deshabilitada por la restricción del registro. | No se atribuye una fecha ni un cuerpo obtenido por un canal alternativo. |

Se contrastaron estas páginas públicas: [El Faro, 28 de septiembre de 2026](https://beta.elfaro.net/el-salvador/trump-bukele-carcel-ruth-lopez), [Diario El Mundo, 20 de septiembre de 2026](https://diario.elmundo.sv/nacionales/el-42-de-las-personas-atendidas-por-la-pddh-durante-agosto-fueron-privados-de-libertad) y [archivo EDH, 10 de junio de 2022](https://historico.elsalvador.com/historico/965496/organizacion-promovera-juicios-violacion-derechos-humanos.html). Sólo se inspeccionaron en memoria; las pruebas del repositorio utilizan HTML ficticio mínimo.

El Faro contiene tarjetas `<article>` que no son el cuerpo de la noticia; no se
recurre a ellas si falta su contenedor editorial. Los párrafos HTML anidados de
El Mundo se recorren una sola vez y en orden, preservando el texto anterior y
posterior a los bloques internos. Los comentarios HTML y nodos marcados como
ocultos quedan fuera de la extracción.

Para los medios HTML se exige una fecha específica del artículo. Un reloj de la
cabecera, un `meta name="date"`, una fecha de modificación o la fecha heredada de
un índice no la sustituyen. El JSON-LD con URL explícita de otra noticia no aporta
la fecha de la actual; si carece de URL, debe identificar el mismo titular. Un `<time itemprop="datePublished">` sólo se considera
dentro del artículo seleccionado. Si no hay fecha válida, se devuelve
`source_publication_date_missing` para revisión, sin inventarla.

Los enlaces antiguos de `www.elsalvador.com` pueden redirigir al dominio histórico
y cambiar de ruta. El extractor mantiene su comprobación de identidad y no
equipara automáticamente rutas diferentes; el colector debe aportar el enlace
canónico del archivo que haya comprobado. Los hosts autorizados son explícitos:
registrar un medio no habilita cualquier subdominio suyo.

En publicaciones de Elementor se selecciona exclusivamente el contenedor
`data-elementor-type="wp-post"` con `data-elementor-post-type="post"`; sus plantillas
de encabezado y pie usan otro tipo y quedan fuera. Se descartan widgets de noticias
relacionadas, biografía, comentarios, metadatos y botones sociales. Esto evita
confundir las tarjetas `<article>` de recomendaciones con el cuerpo de la noticia.

No se sortean captchas, restricciones de acceso, contraseñas ni muros de pago.
Una fuente marcada `access: restricted` se detiene antes de realizar HTTP.
Respuestas 401, 402, 403, 429 o 503 se registran como fuente restringida, sin intentar
otro canal para obtener el contenido. Un bloqueo reconocido en WordPress tampoco
dispara una lectura alternativa del HTML. El cuerpo embebido en JSON-LD nunca se
usa para eludir restricciones; JSON-LD solo puede aportar la fecha de publicación.

Cada consulta usa HTTPS con verificación TLS, resolución DNS pública, redirecciones
solo al mismo medio (máximo tres) y un máximo de 1 MB de respuesta descomprimida.
Las sesiones autenticadas se rechazan y se desactiva el uso implícito de `.netrc`.
El documento enviado al extractor se limita a 24.000 caracteres, 160 párrafos y
4.000 caracteres por párrafo; `truncated: true` señala cualquier recorte. Menos de
200 caracteres útiles produce un error, no un borrador inventado.

La salida contiene:

```text
source_url, title, published_date, text,
paragraphs: [{id: "p1", text: "…"}],
content_sha256, fetched_at, extraction_method, truncated
```

`content_sha256` es SHA-256 del **texto normalizado exacto** retornado en `text`,
incluidos los separadores de dos saltos de línea. No es el hash del HTML original.
`fetched_at` indica recuperación; no se usa como fecha de publicación ni del hecho.
La fecha procede del medio (WordPress, metadatos HTML o JSON-LD). En las fuentes
WordPress existentes se conserva el fallback a los metadatos previos de la
referencia y, si tampoco hay fecha válida, permanece `null`. Las nuevas fuentes
HTML requieren la fecha propia del artículo, como se describe arriba.

`SourceFetchError.code` devuelve códigos breves sin cuerpos ni secretos. El flujo
que invoque este módulo debe conservar únicamente la trazabilidad y las citas
breves necesarias en el borrador privado, descartar el texto al terminar y mantener
la aprobación humana antes de publicar.
