# Lectura de fuentes para borradores privados

`mdh_pipeline.article_source.fetch_article(candidate)` recupera texto público de
Diario Co Latino, GatoEncerrado y Revista Factum para preparar un borrador que deberá
revisarse. No llama a Gemini, no publica fichas y no guarda cuerpos en disco, en
GitHub ni en el archivo bibliográfico público.

La entrada es una referencia existente de la cola o del archivo: `source_url`,
`publisher`, `source_title` o `title`, y `published_date`. Si existe
`discovery.source_post_id`, se consulta ese identificador en la API WordPress
configurada para el medio; **nunca se usa un endpoint recibido de la noticia**.
En otro caso se intenta una sola consulta acotada por slug. En ambos caminos el
enlace declarado por WordPress debe coincidir con la noticia original; solo se
admite la variante `www` configurada y la barra final.

Si la API pública no existe o no aporta texto utilizable, se intenta el HTML
público de la misma noticia, dentro de un contenedor semántico de artículo. Se
eliminan navegación, scripts, estilos, formularios, anuncios, contenidos relacionados
y controles de compartir. Se preservan párrafos, encabezados y elementos de lista
como unidades numeradas `p1`, `p2`, etc. Estos identificadores corresponden a la
versión normalizada recuperada, no a la numeración editorial del medio.

En publicaciones de Elementor se selecciona exclusivamente el contenedor
`data-elementor-type="wp-post"` con `data-elementor-post-type="post"`; sus plantillas
de encabezado y pie usan otro tipo y quedan fuera. Se descartan widgets de noticias
relacionadas, biografía, comentarios, metadatos y botones sociales. Esto evita
confundir las tarjetas `<article>` de recomendaciones con el cuerpo de la noticia.

No se sortean captchas, restricciones de acceso, contraseñas ni muros de pago.
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
La fecha procede del medio (WordPress, metadatos HTML o JSON-LD) o de los metadatos
previos de la referencia. Si no existe una fecha válida, permanece `null`.

`SourceFetchError.code` devuelve códigos breves sin cuerpos ni secretos. El flujo
que invoque este módulo debe conservar únicamente la trazabilidad y las citas
breves necesarias en el borrador privado, descartar el texto al terminar y mantener
la aprobación humana antes de publicar.
