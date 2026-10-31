# Contribuir a MDH-SV

**English summary.** MDH-SV is a public-interest monitor of human-rights news
in El Salvador. You can report a wrong record through the issue forms, use the
private channels in [SECURITY.md](SECURITY.md) for sensitive corrections or
vulnerabilities, or contribute code, sources and documentation following this
guide: clone, run `python3.13 scripts/bootstrap.py`, make one focused change on
a `feat/` or `bug/` branch with a failing test first, run the narrow tests and
`.venv/bin/python scripts/check.py`, then open a small pull request. Docs are
in Spanish; commits and PR titles in English. Code is MIT-licensed and own
metadata CC BY 4.0. Details below.

Al contribuir aceptas el [Código de Conducta](CODE_OF_CONDUCT.md) y las
licencias del final de esta guía. Este documento cubre contribuciones al
proyecto público; la cola editorial privada no forma parte del alcance.

## Formas de contribuir

- **Errores no sensibles**: los formularios públicos de issues descritos en
  [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/) piden el identificador o
  la URL pública y una explicación sin datos sensibles.
- **Correcciones sensibles, retiradas y vulnerabilidades**: usa los canales
  privados de [SECURITY.md](SECURITY.md). Nunca publiques datos sensibles en un
  issue, PR o discusión; son públicos.
- **Código, fuentes y documentación**: sigue el flujo de trabajo de esta guía.
  La incorporación de fuentes nuevas sigue además el procedimiento de
  [docs/SOURCES.md](docs/SOURCES.md#añadir-o-modificar-una-fuente).

## Entorno

- Python 3.13 y Node 22 (≥ 22.12), con el repositorio clonado completo.
- `python3.13 scripts/bootstrap.py` prepara el checkout sin credenciales:
  crea `.venv`, instala las dependencias Python y npm fijadas, descarga la
  release autenticada de Gitleaks y el Chromium de Playwright (sólo las
  dependencias fijadas en `apps/web/package.json`, nunca `npx`). No sobrescribe
  archivos `.env` ni entornos existentes. El arranque verifica las versiones
  de Python y Node y explica la corrección (por ejemplo, `nvm use` cuando nvm
  está instalado).
- `.venv/bin/python scripts/check.py` ejecuta la comprobación completa;
  `--audit` añade las comprobaciones de seguridad (secretos, dependencias,
  límites de publicación).

## Mapa del repositorio

- `packages/schema` — esquemas compartidos y contratos entre consumidores.
- `apps/pipeline` — pipeline Python de recolección, historia, selección y fichas.
- `apps/mcp` — servidor MCP Python.
- `apps/web` — sitio público (Vite/React) y sus pruebas Vitest y Playwright.
- `server` — servidor público Node.js (HTTP, API pública, MCP remoto).
- `api` — funciones Vercel: endpoints públicos bajo `api/v1/` e internos.
- `shared` — validadores y esquemas JavaScript compartidos entre servidor y web.
- `data/` — datos publicados: `history`, `selection`, `automated`,
  `official-statistics`, `ngo_reports`, `geo`, `reviewed`, `editorial-corpus`.
- `ops/` — plantillas de automatización y operación; **no son workflows
  activos** (la promoción sigue [docs/GITHUB-ACTIONS.md](docs/GITHUB-ACTIONS.md)).
- `scripts` — herramientas de desarrollo, comprobación y publicación.
- `tests` — pruebas Python (pytest) y JavaScript (node:test, Vitest, Playwright).
- `docs` — documentación pública en español.

## Flujo de trabajo

1. Crea una rama con prefijo `feat/`, `bug/`, `docs/`, `ci/`, `refactor/` o
   `chore/` en kebab-case. Los `push` que ejecutan CI son los de `main`,
   `feat/**`, `bug/**` y `chore/automated-**` (`.github/workflows/ci.yml`);
   cualquier pull request ejecuta `verify` independientemente del prefijo.
2. Escribe primero una prueba que falle. Haz commits pequeños con mensajes
   Conventional Commits en inglés (`feat:`, `fix:`, `test:`, `docs:`, …) y
   abre PRs de una sola preocupación.
3. Ejecuta las pruebas acotadas del área tocada:

   ```sh
   # Contratos y pipeline (Python)
   .venv/bin/python -m pytest tests/test_catalogue_contract.py tests/test_public_contracts.py tests/test_source_registry.py -q
   # API pública y contratos OpenAPI (Node)
   npm run test:api
   # Web (Vitest; pruebas de navegador con Chromium local)
   npm --prefix apps/web test
   npm --prefix apps/web run test:browser
   # Corpus editorial
   .venv/bin/python -m mdh_pipeline.editorial_corpus check
   .venv/bin/python -m pytest -q tests/test_editorial_corpus.py
   ```

   Tras P2.1 (#249) también aplican
   `tests/api-budgets.test.mjs` (incluido en `npm run test:api`) y
   `npm --prefix apps/web run check:bundle`.
4. Antes del PR, `.venv/bin/python scripts/check.py` completo.
5. CI (`verify`): lint, pruebas Python y JavaScript, contratos, compilación de
   la web y comprobación de datos generados.
6. Revisión: un PR por preocupación, `verify` estricto en verde sobre el head,
   todas las conversaciones resueltas, integración con historia lineal
   (rebase). `.github/CODEOWNERS` exige propietario en todo el árbol.

## Reglas de datos

- **No edites a mano los snapshots generados**. Estas rutas exactas de
  `.github/CODEOWNERS` sólo las integra el controlador de publicación:

  ```
  /data/history/co-latino.json
  /data/history/gatoencerrado.json
  /data/history/factum.json
  /data/history/el-faro.json
  /data/history/el-mundo.json
  /data/history/la-prensa-grafica.json
  /data/history/el-diario-de-hoy.json
  /data/selection/news.json
  /data/selection/news_exceptions.json
  /data/automated/news_dossiers.json
  /apps/web/public/archive/news.json
  /apps/web/public/archive/dossiers.json
  /apps/web/public/archive/locations.json
  /apps/web/public/archive/processing.json
  ```

- El catálogo de registros sólo cambia por revisión editorial.
- Sin cuerpos de artículos, datos de la cola privada ni nombres de personas
  privadas en código, fixtures, issues o PRs.
- Las correcciones editoriales siguen [ops/review-queue/README.md](ops/review-queue/README.md).
- Nunca inventes datos: lo desconocido queda explícito (campos, estados y
  notas), no inferido.

## Contratos y automatización

- Cualquier cambio de forma pública sigue
  [docs/CONTRACTS.md](docs/CONTRACTS.md#añadir-una-regla-o-un-ejemplo):
  fixtures compartidas en ambos consumidores, OpenAPI actualizado y sin relajar
  `additionalProperties`.
- Las GitHub Actions van fijadas por SHA: ver
  [docs/GITHUB-ACTIONS.md](docs/GITHUB-ACTIONS.md). La regla de coste cero del
  proyecto está en [docs/QUALITY.md](docs/QUALITY.md): nada de servicios de
  pago ni pasos que consuman la cuota gratuita sin presupuesto.

## Licencias

El código nuevo se aporta bajo la licencia [MIT](LICENSE); los metadatos y
resúmenes propios bajo [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Las contribuciones se aceptan bajo las mismas licencias. Los documentos
originales enlazados conservan los derechos de sus autores.
