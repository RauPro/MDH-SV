# MDH-SV · Monitor de Derechos Humanos de El Salvador

Monitor independiente de **reportes de presuntas violaciones de derechos humanos** para consulta pública, investigación y seguimiento institucional. Cada registro identifica su fuente y un localizador dentro del documento. La existencia de una fuente no equivale a corroboración independiente ni a una determinación judicial.

La edición inicial contiene 17 reportes individuales, 2 reportes colectivos y 5 informes de contexto. Es una muestra documental limitada, con revisión humana pendiente. No mide prevalencia, no constituye un censo y no suma personas entre fuentes. Las organizaciones citadas no patrocinan ni validan el proyecto.

## Integridad de la publicación

El prototipo contenía registros sintéticos atribuidos a organizaciones reales. Se excluyeron del conjunto público. Sus datos y el sistema anterior se conservan **solo localmente**, fuera del historial de publicación. El historial antiguo también contenía una credencial incrustada: esta publicación comienza con un historial limpio y no incluye esa credencial. Su titular debe revocarla en Google si aún existe.

- Fuente pública canónica: `data/reviewed/publications.json`.
- Validador y exportador: `python -m mdh_pipeline.publication`.
- Las geometrías públicas son nulas; el departamento no representa un lugar preciso.
- El manifest SHA-256 comprueba los archivos exportados, no la veracidad de los hechos.
- La recolección cada seis horas registra noticias en una cola privada de GitHub, sin publicar ni actualizar la web.
- La API REST y el MCP remoto consultan exclusivamente las fichas y el archivo bibliográfico públicos.

## Desarrollo y pruebas

Requiere Python 3.12+ y Node 22.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e . --no-deps
.venv/bin/pytest -q
.venv/bin/python -m mdh_pipeline.publication
```

La API REST está en [`/api/v1/records`](https://mdh-sv.vercel.app/api/v1/records), [`/api/v1/news`](https://mdh-sv.vercel.app/api/v1/news) y [`/api/v1/stats`](https://mdh-sv.vercel.app/api/v1/stats). El MCP remoto usa **https://mdh-sv.vercel.app/mcp**, transporte Streamable HTTP, lectura anónima sin clave. [Contrato OpenAPI](https://mdh-sv.vercel.app/api/v1/openapi.json) y [guía de integración](docs/API.md).

Para desarrollo: `npm ci`, `npm run test:api`, `npm run dev:api`. El módulo Python `.venv/bin/python -m mdh_mcp.server` conserva el transporte local `stdio`.

## Uso responsable

Contraste cada reporte con su documento original. No use las fichas para determinar culpabilidad, elegibilidad o riesgo de personas. Para errores no sensibles, abra un issue con el identificador del registro. No adjunte denuncias, nombres de víctimas ni documentos privados: el repositorio es público y no es un canal confidencial ni de emergencia.

Código nuevo bajo MIT; metadatos y resúmenes propios bajo CC BY 4.0. Los documentos originales conservan los derechos y condiciones de sus autores.

## Portal y despliegue

```sh
npm ci
npm run test:api
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run dev
```

Vercel usa la raíz del repositorio y `vercel.json`. No necesita variables de entorno. El build verifica que todas las descargas correspondan al conjunto canónico. Más detalles en [operación](docs/OPERATIONS.md), [metodología](docs/METHODOLOGY.md) y [seguridad](SECURITY.md).


## Recolección y revisión editorial

La cola está en el repositorio privado `RauPro/MDH-SV-review`. Recoge Co Latino, GatoEncerrado y Revista Factum cada seis horas. Las candidatas se deduplican por URL y conservan su decisión editorial. La aprobación manual exige identidad de revisor, comprobación de privacidad y una ficha validada; genera una propuesta privada, nunca un despliegue automático.

Los workflows reproducibles y las instrucciones de revisión están en [ops/review-queue](ops/review-queue/README.md). Para importar una propuesta aprobada: `python -m mdh_pipeline.review_import --candidate IDENTIFICADOR --repository RauPro/MDH-SV-review`. El comando verifica la aprobación vigente y prepara cambios locales para revisión y CI.

## Continuidad de la investigación WSDS

El monitor amplía la implementación del artículo [Generative AI-based tool for efficient processing of Human Rights violations-related news](https://ieeexplore.ieee.org/document/10933836), DOI `10.1109/CONCAPAN63470.2024.10933836`. Se verificó el resumen y la fecha **Date Added to IEEE Xplore: 26 de marzo de 2025**; el texto completo requiere suscripción. La correspondencia de los campos de investigación se contrastó con el código original conservado localmente.

- **Archivo desde el paper:** 956 referencias iniciales entre 26/03/2025 y 09/10/2026, seleccionadas entre 7.695 entradas de Co Latino, GatoEncerrado y Factum. La vista inicial muestra 279 referencias con indicios textuales de relación con El Salvador; otras 604 quedan por determinar y 73 se clasifican como contexto internacional. La clasificación es automática, no confirma jurisdicción. Las cifras evolucionan con la recolección. Cobertura parcial, con meses y fuentes visibles; las referencias aún requieren lectura, evaluación de pertinencia y revisión. No se cuentan como violaciones.
- **Fichas estructuradas:** hechos, personas afectadas, lugar, fechas, actores, fuentes, temas, grupos, hipótesis atribuida e instrumentos. Cada valor tiene estado y evidencia; los datos protegidos o no consignados quedan explícitos. [Esquema](docs/DOSSIER-SCHEMA.md).
- **Reutilización:** API REST y MCP remoto de solo lectura con filtros, paginación, atribución y estado editorial, para proyectos externos.

El archivo se actualiza cada seis horas. La cola histórica incorpora hasta veinte referencias por ejecución para revisión privada, además de las candidatas RSS. No publica fichas automáticamente. [Colector y cobertura](ops/history/README.md), [borradores privados](docs/PRIVATE-DRAFTS.md).
