# Fijación y actualización de GitHub Actions

## Política

Todas las referencias remotas `uses:` de los workflows públicos y las plantillas
de `ops/` deben usar el SHA completo del commit revisado. Se aplica también a
jobs de lectura para que una futura ampliación de permisos no deje una excepción
implícita. El comentario conserva la versión exacta para revisión y Dependabot:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
```

No usar tags, ramas, SHA cortos ni expresiones para seleccionar código remoto.
Los workflows reutilizables remotos siguen la misma regla. Las Actions de
contenedor, si se introducen, requieren `@sha256:` y un digest completo.

`tests/test_action_pins.py` comprueba todos los workflows de `.github/workflows/`
y las plantillas bajo `ops/`, incluidas carpetas ocultas. Sigue las referencias
locales a workflows y Actions compuestas y rechaza escapes, ciclos y manifiestos
ausentes. Las plantillas planas de `ops/` necesitan definir su ubicación de
instalación antes de introducir una referencia local. El check obligatorio
`verify` ejecuta estas pruebas.

La comprobación inspecciona `uses`, no el código interno de Actions remotas ni
descargas arbitrarias de un bloque `run`. Fijar una Action tampoco fija por sí
solo la imagen del runner, Python, Node o paquetes descargados. Se mantienen los
controles de dependencias y el aislamiento descritos en
[PUBLICATION-SECURITY.md](PUBLICATION-SECURITY.md).

## Revisar una actualización

1. Revisar la release y el diff en el repositorio oficial del proveedor. Valorar
   cambios de permisos, entradas, runtime del Action, requisitos del runner y
   dependencias transitivas. Un comentario de versión no autentica el commit.
2. Resolver el tag exacto en ese repositorio. Por ejemplo, con GitHub CLI:

   ```sh
   gh api repos/actions/checkout/git/ref/tags/v4.4.0 \
     --jq '{type: .object.type, sha: .object.sha}'
   gh api repos/actions/checkout/commits/11d5960a326750d5838078e36cf38b85af677262 \
     --jq '{sha: .sha, url: .html_url}'
   ```

   Si `object.type` es `tag`, consultar `repos/OWNER/REPO/git/tags/TAG_OBJECT_SHA`
   y seguir su `object` hasta llegar a `commit`. El SHA del objeto tag no es el
   commit que debe fijarse. Contrastar el resultado con la release oficial;
   no copiar un SHA de un fork, comentario o resultado de búsqueda.
3. Cambiar SHA y comentario de versión en todas las referencias afectadas:

   ```sh
   git grep -n 'uses:.*actions/checkout@' -- .github/workflows ops
   ```

   Tratar cada versión mayor por separado. Por ejemplo, `setup-node` conserva
   v4 en CI y v5 en otros workflows; fijarlas no autoriza cambiar su versión
   mayor. Mantener idénticas las copias de `refresh-history.yml` en `.github/`
   y `ops/history/`.
4. Ejecutar los controles y revisar el diff antes del commit:

   ```sh
   .venv/bin/pytest -q tests/test_action_pins.py tests/test_history_scan_workflows.py \
     tests/test_news_issue_workflow.py tests/test_official_statistics_workflows.py
   git diff --check
   ```

   Cuando cambie el comportamiento del Action, ejecutar además las pruebas del
   flujo afectado. No modificar secretos, permisos, disparadores o código
   aprobado incidentalmente al actualizar una dependencia.
5. Abrir una PR pequeña con el origen oficial del SHA, versión anterior/nueva,
   alcance y resultados. Esperar `verify` y la revisión exigida por `main`.
   El bot publicador no puede integrar cambios de workflows: sólo sus JSON
   permitidos. No crear un bypass para actualizaciones de dependencias.

## Dependabot y plantillas

La configuración pública de Dependabot revisa GitHub Actions mensualmente.
Dependabot admite referencias por SHA y actualiza el comentario de versión
cuando está en la misma línea. Su entrada `directory: /` busca workflows en
`.github/workflows` y el manifiesto Action de la raíz; no actualiza por sí sola
las copias operativas bajo `ops/` ni el repositorio privado. El mantenedor debe
sincronizar esas referencias en la misma revisión y promoverlas después.

Referencias oficiales: [fijación y mantenimiento seguro](https://docs.github.com/en/actions/reference/security/secure-use),
[alcance de directorios de Dependabot](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#directories-or-directory).

## Promover a la cola privada

Versionar una plantilla pública no cambia el workflow instalado. La promoción
en `RauPro/MDH-SV-review` se realiza en una rama nueva y una PR separada, después
de integrar y verificar la fuente pública.

| Plantilla pública | Destino privado |
| --- | --- |
| `ops/review-queue/.github/workflows/*.yml` | `.github/workflows/`, mismo nombre |
| `ops/history/review-history.yml` | `.github/workflows/history.yml` |
| `ops/official-statistics-review/monitor.yml` | `.github/workflows/official-statistics-monitor.yml` |
| `ops/official-statistics-review/review.yml` | `.github/workflows/official-statistics-review.yml` |

Antes de escribir, leer el `main` privado y los blobs instalados. Si hay cambios
desde la revisión, reconciliarlos; no sobrescribirlos con una copia antigua.
Usar comparación de SHA al escribir o un commit con padre conocido y push
normal. Revisar el diff completo de la PR privada.

Para una actualización exclusiva de Actions, conservar cada `ref` de código
público que ya estaba aprobado: los distintos workflows pueden usar SHA
diferentes. Sustituir el marcador `MDH_PUBLIC_CODE_REF` por ese valor instalado
al preparar la plantilla. Promover código nuevo es una operación distinta y
requiere su propia revisión. Tampoco sustituir `ref: main` de los checkouts de
datos ni cambiar credenciales, permisos o repositorios de destino.

Comprobar tras el merge que cada workflow privado coincide con su plantilla,
salvo la sustitución explícita del marcador. Conservar el job publicador aislado,
sus permisos vacíos y el escaneo de historial anterior al primer push.

## Exigencia remota y comprobación operativa

Después de que todos los workflows instalados estén fijados, activar
`sha_pinning_required` en ambos repositorios. Leer primero
`GET /repos/OWNER/REPO/actions/permissions`; al actualizar con `PUT`, conservar
`enabled`, `allowed_actions` y cualquier selección vigente. Cambiar sólo la
exigencia de SHA y confirmar el resultado con otro GET. No activar esta regla
antes de instalar los pins ni ampliar la lista de Actions permitidas.

La [política remota de Actions](https://docs.github.com/en/rest/actions/permissions#set-github-actions-permissions-for-a-repository)
complementa las pruebas versionadas; guardar YAML no configura GitHub.

Ejecutar `Publicar ubicaciones automáticas` en el `main` privado con
`validation_only=true`, `source_check_limit=20` y `automatic_validation_limit=1`.
Esto reutiliza recibos: no inicia nuevas llamadas a Gemini ni a fuentes. Verificar
preparación, runner aislado, escaneo previo al push, CI del SHA exacto, PR,
integración normal y despliegue. Un run que sólo propone una rama no acredita
que los datos estén publicados.

Si se actualizó un productor público, ensayarlo también: por ejemplo,
`refresh-history.yml` con `max_pages=1`. Hacer los ensayos secuencialmente. Ante
un conflicto con otra actualización, regenerar desde la base actual; nunca
sobrescribir snapshots, forzar un merge ni desactivar las reglas de `main`.

## Reversión

Si una actualización de Action falla, revertir mediante PR al SHA anterior
revisado, tanto en la fuente pública como en las instalaciones afectadas.
Conservar la exigencia de SHA, los checks y el aislamiento. Repetir el ensayo
operativo y comprobar la versión instalada. No volver a un tag mutable como
solución de emergencia.
