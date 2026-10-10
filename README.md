# MDH-SV · Monitor de Derechos Humanos de El Salvador

Monitor independiente de **reportes de presuntas violaciones de derechos humanos** para consulta pública, investigación y seguimiento institucional. Cada registro identifica su fuente y un localizador dentro del documento. La existencia de una fuente no equivale a corroboración independiente ni a una determinación judicial.

La edición inicial contiene cuatro reportes individuales, un reporte colectivo y cuatro informes de contexto. Es una muestra documental limitada, con revisión humana pendiente. No mide prevalencia, no constituye un censo y no suma personas entre fuentes. Las organizaciones citadas no patrocinan ni validan el proyecto.

## Integridad de la publicación

El prototipo contenía registros sintéticos atribuidos a organizaciones reales. Se excluyeron del conjunto público. Sus datos y el sistema anterior se conservan **solo localmente**, fuera del historial de publicación. El historial antiguo también contenía una credencial incrustada: esta publicación comienza con un historial limpio y no incluye esa credencial. Su titular debe revocarla en Google si aún existe.

- Fuente pública canónica: `data/reviewed/publications.json`.
- Validador y exportador: `python -m mdh_pipeline.publication`.
- Las geometrías públicas son nulas; el departamento no representa un lugar preciso.
- El manifest SHA-256 comprueba los archivos exportados, no la veracidad de los hechos.
- La ingesta local produce candidatos privados, sin publicar ni actualizar la web.
- El servidor MCP consulta exclusivamente el conjunto público de solo lectura.

## Desarrollo y pruebas

Requiere Python 3.12+ y Node 22.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e . --no-deps
.venv/bin/pytest -q
.venv/bin/python -m mdh_pipeline.publication
```

El módulo MCP se inicia desde el repositorio con `.venv/bin/python -m mdh_mcp.server`. Transporte local `stdio`. No se ofrece un endpoint MCP remoto activo.

## Uso responsable

Contraste cada reporte con su documento original. No use las fichas para determinar culpabilidad, elegibilidad o riesgo de personas. Para errores no sensibles, abra un issue con el identificador del registro. No adjunte denuncias, nombres de víctimas ni documentos privados: el repositorio es público y no es un canal confidencial ni de emergencia.

Código nuevo bajo MIT; metadatos y resúmenes propios bajo CC BY 4.0. Los documentos originales conservan los derechos y condiciones de sus autores.

## Portal y despliegue

```sh
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run dev
```

Vercel usa la raíz del repositorio y `vercel.json`. No necesita variables de entorno. El build verifica que todas las descargas correspondan al conjunto canónico. Más detalles en [operación](docs/OPERATIONS.md), [metodología](docs/METHODOLOGY.md) y [seguridad](SECURITY.md).
