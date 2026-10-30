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
fijado en el lock, con `--strict --no-deps --disable-pip`, sobre `requirements.lock`
y `ops/review-queue/requirements.txt` por separado. Se consultan las versiones
exactas sin instalar ni ejecutar paquetes para resolverlas. No se ignoran avisos.
El escaneo de dependencias no sustituye el análisis del código ni de secretos.

## Actualizaciones

Dependabot revisa semanalmente npm raíz/web y pip raíz/cola privada. El fichero
raíz `requirements.txt` incluye `requirements.lock`; conserve esa relación para
que las actualizaciones lleguen al entorno instalado. Las Actions siguen su
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
