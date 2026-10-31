# Calidad y dependencias

## Base de lint

La base comprobada no tiene diagnósticos pendientes ni un archivo de excepciones
históricas. Ruff usa la configuración explícita del repositorio; no depende de
las preferencias globales del mantenedor:

```sh
.venv/bin/python -m ruff check --config pyproject.toml .
npm run lint
npm --prefix apps/web run lint
```

Python: reglas `E4`, `E9`, `F` y `B023` sobre módulos, scripts y pruebas. Detectan
imports inválidos, errores de sintaxis, nombres sin definir, variables sin uso y
cierres sobre variables de bucle. No se impone una reformateación masiva ni reglas
adicionales de estilo en este punto del roadmap. Añadir familias de reglas exige
revisar y corregir sus diagnósticos en el mismo cambio.

El lint raíz cubre el JavaScript activo de API, servidor, contratos compartidos,
scripts y pruebas, incluidos nuevos archivos bajo la raíz. `apps/web` usa su propio
lint de React/TypeScript; `legacy` contiene la web archivada y queda fuera del lint
JS activo. Ambos comandos rechazan advertencias. Las únicas supresiones nuevas son
locales y explicadas: expresiones regulares que rechazan caracteres de control
intencionadamente en la frontera de datos públicos.

## Comprobación reproducible

```sh
python3.13 scripts/bootstrap.py
.venv/bin/python scripts/check.py --audit
```

CI ejecuta esos mismos comandos, con Python 3.13 y Node 22. Antes de preparar el
entorno, CI instala el escáner fijado y revisa todo el historial publicable. El
comando común vuelve a autenticar el escáner, ejecuta lint, tests, validación de
instancia/datos, comparación de exports temporales y build. No modifica datasets.
Sin `--audit` no consulta servicios de avisos; con `--audit`, cualquier fallo de
consulta o hallazgo bloqueante hace fallar la comprobación.

Las auditorías npm incluyen dependencias de desarrollo en raíz y web y bloquean
avisos moderados o mayores. Python usa [pip-audit](https://github.com/pypa/pip-audit)
fijado en el lock, con `--strict --no-deps --disable-pip`, sobre `requirements.lock.txt`
y `ops/review-queue/requirements.txt` por separado. Se consultan las versiones
exactas sin instalar ni ejecutar paquetes para resolverlas. No se ignoran avisos.
El escaneo de dependencias no sustituye el análisis del código ni de secretos.

## Pruebas de integración y navegador

`tests/test_offline_flow.py` recorre la cadena completa de publicación —colección,
clasificación de pertinencia, extracción, selección, evaluación automática,
ubicaciones, reconciliadores y API pública— en directorios temporales con fakes de
HTTP, GitHub y Gemini; no requiere red ni credenciales. Demuestra que el flujo
privado→público produce sólo proyecciones públicas, que los marcadores privados no
se filtran y que una segunda ejecución es idempotente. No demuestra el
comportamiento del sitio desplegado ni el contenido real de las fuentes.

`npm --prefix apps/web run test:browser` ejecuta Playwright sobre el `dist`
construido, servido por una réplica local que aplica las cabeceras y reescrituras
de `vercel.json` y despacha `/api/*` y `/mcp` a los handlers reales. Las recorridos
cubren el monitor de noticias, filtros, el diálogo de ficha, los reportes
documentados, el archivo, las estadísticas, la vista de fallo, el tema, el modo
móvil y los contratos HTTP del réplica; además, axe-core evalúa las vistas clave
con reglas WCAG 2 A/AA y 2.1 A/AA (fallan sólo hallazgos serious o critical).
Demuestra que el build funciona sin servicios de producción y que las
superficies principales cumplen esas reglas en este recorrido. No es una
certificación WCAG ni supervisa el sitio en producción; la detección de
violaciones CSP, peticiones externas y errores de consola se limita a las rutas
cubiertas.

## Actualizaciones

Dependabot revisa semanalmente npm raíz/web y pip raíz/cola privada. El fichero
raíz `requirements.txt` incluye `requirements.lock.txt`; conserve esa relación para
que las actualizaciones lleguen al entorno instalado. El antiguo requirements.lock
se conserva sólo como entrada compatible que incluye ese mismo fichero. La extensión
.txt permite que el lector de requisitos de Dependabot descubra todas las versiones. Las Actions siguen su
procedimiento separado en [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md).

Para una actualización Python, cambie las versiones afectadas y resuelva con pip
sobre Python 3.13 manteniendo restricciones para las restantes. Revise también sus
transitivas y la coherencia del subconjunto `ops/review-queue/requirements.txt`.
Incluya en el lock las herramientas de comprobación y todas sus dependencias,
así como los requisitos de build de pyproject.toml. Bootstrap instala el proyecto
sin aislamiento de build, usando exclusivamente esas herramientas ya fijadas.
Ejecute bootstrap, `pip check` y la comprobación completa antes de integrar.
Para npm, actualice el manifiesto si cambia un pin exacto y regenere el lock del
mismo directorio; evite `audit fix --force` y revise el diff y las pruebas.

Los SHA privados de código aprobado no avanzan al cambiar estos locks: sus entornos
siguen usando el lock de cada SHA. La auditoría de esta rama cubre los manifiestos
actuales, no certifica esos snapshots históricos. Promover código en la cola
privada requiere seguir GITHUB-ACTIONS.md, revisar cada workflow instalado y
verificar su ejecución; no reemplace todos los pins por uno de forma automática.

## Operación con presupuesto cero

El proyecto debe mantenerse sin coste. No habilite gasto adicional ni añada
servicios de auditoría de pago para superar estos controles. El límite de gasto
de Actions debe permanecer en $0 con bloqueo al alcanzarlo. Los runners privados
pueden dejar de arrancar al agotarse la cuota incluida; en ese caso registre la
verificación privada como pendiente hasta su renovación, sin presentarla como
aprobada. El histórico público y las comprobaciones locales se verifican por
separado. Cambiar la frecuencia de los bots requiere acordar esa decisión operativa. La cadencia programada de la cola privada está dimensionada para la cuota incluida: `ops/actions-minutes-budget.json` modela el peor caso mensual y `tests/test_actions_minutes_budget.py` lo verifica contra los horarios reales.

## Carga de datos y presupuestos de respuesta

`tests/api-budgets.test.mjs` acota cada respuesta pública; los umbrales dejan
margen a la cadencia automatizada de datos. Valores impresos por la prueba el
2026-10-31 sobre los datos del commit `975daf5`:

| Respuesta | Medido | Presupuesto |
| --- | --- | --- |
| `/api/v1/news?limit=1&coverage=summary` | 16 399 B | 32 KiB |
| `search_news` MCP `{limit:1}` | 33 972 B | 64 KiB |
| `/api/v1/news?coverage=summary` | 179 508 B | 384 KiB |
| `/api/v1/news?limit=100&coverage=summary` | 459 479 B | 1 MiB |
| `/api/v1/news/coverage` | 400 430 B | 1 MiB |

Latencia medida en la misma ejecución: importación más primera consulta con
`coverage=summary`, 451,0 ms (presupuesto 3 000); p95 en caliente sobre 40
consultas mixtas, 3,53 ms (presupuesto 150). La vista inicial transporta
`news.json` (3 904 537 B, 262 993 brotli), `dossiers.json` (4 557 992 B,
202 568), `locations.json` (382 889 B, 41 751), `processing.json` (829 508 B,
180 497) y los activos JS/CSS (396 975 B, 101 666 brotli; 33 920 B, 6 088).
`check:bundle` fija el tope de JS gzip en 149 444 bytes (medido 119 577 en la
misma ejecución). Pendiente («help wanted»): cargar el detalle de las fichas de
noticia bajo demanda en lugar del snapshot completo de `dossiers.json`.
