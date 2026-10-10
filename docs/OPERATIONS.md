# Operación y despliegue

## Producción

Vercel importa `RauPro/MDH-SV`, rama `main`, raíz `.` y `vercel.json`. Ejecuta `npm ci && npm --prefix apps/web ci`, el build del portal y las funciones Node de `api/`. Node 22.x. Sirve los archivos estáticos de `apps/web/dist` y la API REST/MCP de solo lectura. No requiere secretos, base de datos, autenticación ni servicios de IA en cada consulta.

La web consume JSON del mismo origen. Tiene CSP, bloqueo de iframes, `nosniff`, política sin referrer y permisos de cámara/micrófono/geolocalización deshabilitados. El portal no incorpora analítica ni tipografías externas. El alojamiento conserva las políticas y registros técnicos propios de Vercel.

El prebuild comprueba la fuente canónica, la correspondencia exacta de registros y los hashes de todas las descargas. Una edición exige regeneración. CI prueba Python, MCP, frontend y estructura accesible; un check HTTP exitoso no certifica veracidad.

## Verificación de una versión

```sh
.venv/bin/pytest -q
.venv/bin/python -m mdh_pipeline.publication
.venv/bin/python scripts/audit_release.py
npm ci
npm run test:api
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run lint
npm --prefix apps/web run build
npm --prefix apps/web audit --audit-level=moderate
```

En el sitio publicado, comprobar portada, filtros, fichas por URL, teclado, móvil, JSON/CSV/GeoJSON/Parquet/manifest, códigos HTTP, headers y que un recurso inexistente devuelva 404. El flujo manual `Check public source links` detecta disponibilidad de fuentes; bloqueos antibot pueden requerir verificación en navegador.

## Actualizar el conjunto

Editar `data/reviewed/publications.json` siguiendo la metodología. Regenerar exportaciones y revisar el diff. Abrir una rama y PR, comprobar CI y realizar revisión editorial antes de merge. El push a `main` activa Vercel. No programar publicación automática de candidatos de IA.

## Reversión y continuidad

Para un error de despliegue, promover el último deployment válido desde Vercel o revertir el commit en GitHub; verificar el manifest y la portada después. Los commits del conjunto público permiten reproducir sus versiones; los candidatos privados nunca se incluyen. No restaurar la base del prototipo ni su exportación de 979 registros.

Los fallos de carga de datos se muestran explícitamente, sin cifras falsas de cero. No hay vigilancia operativa humana 24/7 ni SLA. La actualización de dependencias se propone con Dependabot; las propuestas requieren revisión y pruebas antes de merge.

## Pendientes organizativos para uso institucional sostenido

Asignar revisión editorial humana con experiencia en derechos humanos, frecuencia de actualización, canal seguro de contacto y protocolo de respuesta a correcciones/retiros. Expandir cobertura con fuentes comprobadas. Estas tareas no quedan sustituidas por una compilación o un despliegue exitoso.

La clave de Google del código legado debe revocarse por su titular. No forma parte de la nueva publicación ni de su historial. Los archivos históricos y datos no revisados permanecen locales en `legacy/`, `data/` y `.local-quarantine/`, además del respaldo temporal de esta auditoría.


## Cola privada operativa

Repositorio: `RauPro/MDH-SV-review`. Workflow `Recoger noticias`, cron `17 */6 * * *` UTC: aproximadamente 00:17, 06:17, 12:17 y 18:17 en El Salvador (GitHub puede retrasar ejecuciones). Usa un commit fijo del código público y un token efímero limitado al repositorio privado. El recolector RSS no usa PAT persistente, no escribe en el monitor público y no depende de cuotas de Gemini. La etapa de extracción asistida sí utiliza la API de Gemini.

Se verificaron los tres RSS: Co Latino, GatoEncerrado y Factum. El Faro no se configuró porque su URL RSS devolvió HTML sin entradas; Amnistía no se usa como RSS por bloqueo HTTP. Estas exclusiones no afectan las fuentes documentales ya enlazadas en las fichas.

La primera ejecución satisfactoria leyó 35 entradas, abrió cinco candidatas y no publicó registros. La cola mantiene descartes y aprobaciones entre ejecuciones. Un fallo total marca el job como fallido; los fallos parciales quedan identificados por fuente en el resumen. Revisar los fallos en Actions según las preferencias de notificación de GitHub.

El workflow manual `Revisar noticia` exige cuenta humana con acceso de escritura y confirmación de revisión de privacidad para aprobar. La propuesta queda en `approved/` y como artefacto privado. El importador vuelve a comprobar el estado de aprobación: una candidatura rechazada o sin propuesta validada no se importa. La publicación sigue requiriendo commit/PR y CI.

Para actualizar código de la cola, cambiar `ref` en ambos workflows privados por el commit revisado; no apuntar a código mutable de pull requests. Los patrones reproducibles están en `ops/review-queue/` y contienen un marcador que debe sustituirse por el SHA al instalar. No son workflows activos del repositorio público.

## Archivo y acceso remoto

`Refresh public news bibliography` actualiza cada seis horas la bibliografía de tres medios, con un periodo objetivo desde el 1 de enero de 2010 hasta el día de ejecución en El Salvador, y deja visible el estado de cada mes. Ese alcance no garantiza que el recorrido histórico esté terminado: comprobar `coverage.start_date`, `coverage.end_date` y los estados mensuales del snapshot, no la fecha del paper. Su commit solo modifica checkpoints y el archivo público, nunca las fichas. Debe verificarse que cada push automático llegue a Vercel. Los errores de fuente conservan el snapshot previo y exponen cobertura parcial; revisar el resumen de Actions.

`Revisar backlog histórico` en el repositorio privado añade hasta veinte referencias antiguas por ejecución. Comparte deduplicación y exclusión mutua con RSS. Sus aprobaciones siguen necesitando importación, revisión de cambios y CI antes de publicación. Para preparar una ficha sin inventar información se puede usar el [generador de borradores privados](PRIVATE-DRAFTS.md).

Comprobar REST `/api/v1/records`, `/api/v1/records/{id}`, `/api/v1/news` y `/api/v1/openapi.json`. El endpoint remoto `/mcp` requiere POST Streamable HTTP: GET devuelve 405 de forma deliberada. Probar con cliente MCP oficial, no inferir disponibilidad por abrir la URL en un navegador. La guía y límites están en [API.md](API.md).

## Procesamiento asistido de candidatas

La extracción corre cada hora en **Preparar fichas con Gemini**, dentro del
repositorio privado. El modelo y el secreto se configuran en Actions, no en Vercel.
La plantilla usa un SHA público fijo y un token limitado a la cola. Procesa hasta
20 noticias por ejecución programada; una ejecución manual puede ajustar el lote
entre 1 y 50. Los archivos privados conservan estado y evitan repetir extracciones
ya terminadas. Ante cuotas, autenticación o configuración del proveedor inválidas,
se detiene el lote y el resumen identifica el motivo sin revelar secretos.

Los errores, revisiones de alcance y revisiones de privacidad requieren atención
del operador. Ningún estado de extracción aprueba una publicación. Véase el
[procedimiento Gemini](GEMINI-REVIEW.md) y las instrucciones privadas de revisión.
