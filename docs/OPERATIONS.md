# Operación y despliegue

## Producción

Vercel importa `RauPro/MDH-SV`, rama `main`, raíz `.` y `vercel.json`. Ejecuta `npm --prefix apps/web ci` y el build del portal; solo publica `apps/web/dist`. Node 22.x. El portal es estático, sin secretos, base de datos expuesta, autenticación, pagos ni servicios de IA en cada consulta.

La web consume JSON del mismo origen. Tiene CSP, bloqueo de iframes, `nosniff`, política sin referrer y permisos de cámara/micrófono/geolocalización deshabilitados. El portal no incorpora analítica ni tipografías externas. El alojamiento conserva las políticas y registros técnicos propios de Vercel.

El prebuild comprueba la fuente canónica, la correspondencia exacta de registros y los hashes de todas las descargas. Una edición exige regeneración. CI prueba Python, MCP, frontend y estructura accesible; un check HTTP exitoso no certifica veracidad.

## Verificación de una versión

```sh
.venv/bin/pytest -q
.venv/bin/python -m mdh_pipeline.publication
.venv/bin/python scripts/audit_release.py
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
