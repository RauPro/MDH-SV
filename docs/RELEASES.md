# Releases y compatibilidad

El código usa [versionado semántico](https://semver.org/lang/es/). La versión
vive en cuatro lugares que `tests/test_release.py` mantiene iguales:
`package.json`, `apps/web/package.json`, `pyproject.toml` y
`server/version.mjs` (`CODE_VERSION`, que también da la versión del servidor
MCP). Para publicar, se suben las cuatro a la vez y se integran en `main`.

## La API es `v1`

La versión del contrato REST va en la ruta (`/api/v1/`). Los cambios aditivos
—campos, parámetros opcionales, endpoints nuevos— se admiten dentro de `v1`.
Los cambios que rompen el contrato sólo ocurren mediante la política de
retirada o un path nuevo (`/api/v2/`); nunca por edición silenciosa.

## Política de retirada

1. Entrada «Obsoleto» en `CHANGELOG.md` con fecha de anuncio y de corte.
2. Documentación del cambio en `docs/API.md`.
3. `deprecated` o descripción equivalente en el contrato OpenAPI.
4. Cabeceras `Deprecation`, `Sunset` y `Link` con `rel="deprecation"` en las
   respuestas afectadas durante la ventana.
5. Al menos **90 días** entre el anuncio y el cambio de valor por defecto o la
   retirada efectiva.
6. El cambio se registra en el primer release publicado en la fecha de corte o
   después, igual que `meta.snapshot.code_version` lo identifica en cada
   respuesta.

Ejemplo aplicado: la cobertura completa implícita de `GET /api/v1/news`
(anunciada el 2026-11-01, corte el 2027-02-01; véase «Obsoleto» en
[CHANGELOG.md](../CHANGELOG.md)).

## Snapshots de datos

Cada archivo publicado declara su `schema_version` y los bots lo actualizan de
forma continua, así que un release fija una **versión de código junto con el
snapshot de datos de su commit**: la release-manifest enumera ruta, bytes,
SHA-256, `schema_version` y `generated_at` de cada fichero de datos
rastreado. Para comprobar la integridad entre plataformas compare
`tar_sha256` (el tar sin comprimir) y los hashes de los datasets —los bytes
gzip dependen de la versión de zlib—, no la suma de los `.tar.gz`.

## Procedimiento de release

Los releases los inicia un mantenedor: se crea el tag `vX.Y.Z` sobre el commit
integrado en `main` y se empuja. `.github/workflows/release.yml` comprueba que
el commit es ancestro de `main`, construye los artefactos dos veces y los
compara byte a byte, verifica el manifiesto con una reconstrucción y publica
los tres archivos con las notas del changelog mediante `gh release create`.
No hay guardia de automatización ni de variables: un tag es una decisión
humana explícita, no un trabajo programado, por eso el workflow no consulta
`MDH_AUTOMATION_ENABLED`.

En local, `scripts/release.py` ejecuta las mismas operaciones:
`build --tag vX.Y.Z --output DIR` exige árbol limpio, versiones iguales al tag
en el commit y la sección `[X.Y.Z]` del changelog; `verify` reconstruye y
compara cada hash; `notes` imprime el cuerpo de la sección.
