# Operación y despliegue

## Producción

Vercel importa `RauPro/MDH-SV`, rama `main`, raíz `.` y `vercel.json`. Ejecuta `npm ci && npm --prefix apps/web ci`, el build del portal y las funciones Node de `api/`. Node 22.x. Sirve los archivos estáticos de `apps/web/dist` y la API REST/MCP de solo lectura. La consulta pública no requiere secretos, base de datos, autenticación ni servicios de IA. El disparador programado utiliza las credenciales separadas descritas en [HOURLY-INGESTION.md](HOURLY-INGESTION.md).

La web consume JSON del mismo origen. Tiene CSP, bloqueo de iframes, `nosniff`, política sin referrer y permisos de cámara/micrófono/geolocalización deshabilitados. El portal no incorpora analítica ni tipografías externas. El alojamiento conserva las políticas y registros técnicos propios de Vercel.

El prebuild comprueba la fuente canónica, la correspondencia exacta de registros y los hashes de todas las descargas. Una edición exige regeneración. CI prueba Python, MCP, frontend y estructura accesible; un check HTTP exitoso no certifica veracidad.

Las actualizaciones automáticas se proponen mediante PR y necesitan el check
`verify` antes de integrarse. El publicador privado prepara y escribe en runners
separados; el código que lee borradores está fijado a un SHA aprobado. Consulte
[publicación aislada y protección de main](PUBLICATION-SECURITY.md) para la
política, la activación y las comprobaciones operativas.

Las Actions se fijan por SHA completo en workflows y plantillas. El procedimiento
para verificar versiones, actualizar con Dependabot y promover los pins a la
cola privada está en [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md).

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

Para el catálogo de casos, editar `data/reviewed/publications.json` siguiendo la metodología. Regenerar exportaciones y revisar el diff. Abrir una rama y PR, comprobar CI y realizar revisión editorial antes de merge. El push a `main` activa Vercel. Este catálogo de casos no admite publicación automática. Las fichas de noticias tienen un flujo separado de publicación automática por campo, descrito en [NEWS-DOSSIERS.md](NEWS-DOSSIERS.md); las estadísticas oficiales conservan su revisión previa. La proyección de ubicaciones es independiente; véase [AUTOMATIC-LOCATIONS.md](AUTOMATIC-LOCATIONS.md).

## Reversión y continuidad

Para un error de despliegue, promover el último deployment válido desde Vercel o revertir el commit en GitHub; verificar el manifest y la portada después. Los commits del conjunto público permiten reproducir sus versiones; los candidatos privados nunca se incluyen. No restaurar la base del prototipo ni su exportación de 979 registros.

Los fallos de carga de datos se muestran explícitamente, sin cifras falsas de cero. No hay vigilancia operativa humana 24/7 ni SLA. La actualización de dependencias se propone con Dependabot; las propuestas requieren revisión y pruebas antes de merge.

## Pendientes organizativos para uso institucional sostenido

La recepción de correcciones sensibles y retiradas corresponde a @RauPro mediante [el canal editorial privado](PRIVATE-REPORTING.md). Las vulnerabilidades técnicas se reciben por el canal privado separado descrito en [SECURITY.md](../SECURITY.md). Sigue pendiente la revisión especializada para el uso institucional del catálogo de casos y las estadísticas. La publicación ordinaria de noticias se automatiza, y no acredita por sí sola los hechos relatados. Expandir cobertura con fuentes comprobadas. Estas tareas no quedan sustituidas por una compilación o un despliegue exitoso.

El 16 de octubre de 2026 se registró la confirmación del titular de que identificó exactamente la clave Google del código legado, la eliminó y Google confirmó la eliminación. [P0.3 queda cerrado](SECRET-HISTORY.md) con esa declaración y los controles de publicación; no se realizó una comprobación independiente con el proveedor. La clave no forma parte de la publicación ni de su historial permitido y no debe restaurarse ni reutilizarse. Los materiales históricos y datos no revisados permanecen fuera de la publicación; el bundle de recuperación se conserva bajo `.git/mdh-quarantine/`, fuera de las referencias publicables.


## Cola privada operativa

Repositorio: `RauPro/MDH-SV-review`. Workflow `Recoger noticias`, cron `17 7,19 * * *` UTC: aproximadamente 01:17 y 13:17 en El Salvador (GitHub puede retrasar ejecuciones). Cada ciclo de extracción también recoge RSS, por lo que la recolección combinada sigue siendo aproximadamente cada seis horas. Usa un commit fijo del código público y un token efímero limitado al repositorio privado. El recolector RSS no usa PAT persistente, no escribe en el monitor público y no depende de cuotas de Gemini. La etapa de extracción asistida sí utiliza la API de Gemini.

Se verificaron los tres RSS: Co Latino, GatoEncerrado y Factum. El Faro no se configuró porque su URL RSS devolvió HTML sin entradas; Amnistía no se usa como RSS por bloqueo HTTP. Estas exclusiones no afectan las fuentes documentales ya enlazadas en las fichas.

La primera ejecución satisfactoria leyó 35 entradas, abrió cinco candidatas y no publicó registros. La cola mantiene descartes y aprobaciones entre ejecuciones. Un fallo total marca el job como fallido; los fallos parciales quedan identificados por fuente en el resumen. Revisar los fallos en Actions según las preferencias de notificación de GitHub.

Para correcciones editoriales solicitadas y propuestas de casos, el workflow manual `Revisar noticia` exige cuenta humana con acceso de escritura y confirmación de revisión de privacidad para aprobar. La propuesta queda en `approved/` y como artefacto privado. El importador vuelve a comprobar el estado de aprobación: una candidatura rechazada o sin propuesta validada no se importa. La importación de esa decisión humana requiere commit/PR y CI. Las fichas automáticas usan otro contrato y se publican sin ejecutar este workflow.

Para actualizar código de la cola, cambiar `ref` en los workflows privados correspondientes por el commit revisado; no apuntar a código mutable de pull requests. Los patrones reproducibles están en `ops/review-queue/` y contienen un marcador que debe sustituirse por el SHA al instalar. No son workflows activos del repositorio público.

## Archivo y acceso remoto

`Refresh public news bibliography` actualiza cada seis horas la bibliografía de los medios habilitados (siete fuentes registradas, incluida La Prensa Gráfica con acceso automatizado restringido), con un periodo objetivo desde el 1 de enero de 2010 hasta el día de ejecución en El Salvador, y deja visible el estado de cada mes. Ese alcance no garantiza que el recorrido histórico esté terminado: comprobar `coverage.start_date`, `coverage.end_date` y los estados mensuales del snapshot, no la fecha del paper. Su commit actualiza checkpoints y bibliografía y reconcilia las proyecciones públicas existentes; no extrae ni aprueba campos nuevos. Debe verificarse que cada push automático llegue a Vercel. Los errores de fuente conservan el snapshot previo y exponen cobertura parcial; revisar el resumen de Actions.

`Revisar backlog histórico` en el repositorio privado añade hasta veinte referencias antiguas por ejecución. Comparte deduplicación y exclusión mutua con RSS. Las fichas de noticias elegibles siguen después el flujo automático de evidencia y privacidad. Las propuestas de casos y las correcciones humanas conservan importación, revisión del cambio y CI. Para preparar una ficha sin inventar información se puede usar el [generador de borradores privados](PRIVATE-DRAFTS.md).

Comprobar REST `/api/v1/records`, `/api/v1/records/{id}`, `/api/v1/news` y `/api/v1/openapi.json`. El endpoint remoto `/mcp` requiere POST Streamable HTTP: GET devuelve 405 de forma deliberada. Probar con cliente MCP oficial, no inferir disponibilidad por abrir la URL en un navegador. La guía y límites están en [API.md](API.md).

## Procesamiento asistido de candidatas

La extracción está programada para ejecutarse dos veces al día en **Preparar fichas con
Gemini**, dentro del repositorio privado. El disparo principal de Vercel está
configurado a las 01:07 y 13:07 UTC y GitHub conserva el respaldo al minuto 23
de esas mismas horas. Comprobar las
invocaciones reales después de cada cambio: el horario configurado no demuestra
que una ejecución concreta haya terminado. El modelo y la clave Gemini se configuran
en Actions, no en Vercel. La plantilla usa un SHA público fijo y un token limitado
a la cola. Solicita hasta 16 solicitudes por ciclo; el límite efectivo se reduce según
el saldo y las horas restantes del día del Pacífico. Cada intento HTTP, incluidos
los reintentos, consume ese presupuesto compartido por clasificación, extracción y evaluación automática.

La configuración privada utiliza `operator_cap`, con un tope propio de 240
solicitudes al día, 2 por minuto y 200 000 unidades conservadoras de entrada por
minuto. El límite diario de Google no está visible (`rpd: null`);
estos topes no se presentan como cuotas confirmadas del proveedor. Un 429 pausa
las llamadas hasta la siguiente medianoche del Pacífico. No se activa facturación
ni se cambia automáticamente de modelo. Los archivos privados conservan estado
y evitan repetir extracciones ya terminadas. Ante autenticación o configuración
inválidas, se detiene el lote y el resumen identifica el motivo sin revelar secretos.

El publicador evalúa hasta cuatro fichas por llamada mediante una segunda pasada
de Gemini, sujeta al mismo registro de cuota. `automatic_validation_limit=50` es
un techo; el cupo efectivo viene de `runtime/news-allocation.json`, vinculado a la
ejecución anterior y al mismo día de cuota, y sólo puede reclamarse una vez. El
reparto considera trabajo elegible: con un cupo de 10 llamadas y tres colas con
trabajo, asigna 2 a clasificación, 6 a extracción y 2 a validación; sin clasificación
pendiente puede asignar 8 y 2 respectivamente. Los disparos manuales también
reciben un cupo proporcional al saldo y las horas restantes, sin reiniciar cuotas.

Las plantillas usan `VALIDATION_BATCH_SIZE=4`, `VALIDATION_LIMIT=50` y
`MDH_ALLOCATION_SOURCE_RUN_ID` para enlazar el publicador con el extractor. El CLI
equivalente es `automatic_news evaluate --batch-size 4 --limit 50`, junto con los
argumentos de archivos y las credenciales privadas existentes. Reducir el tamaño
de lote no modifica la cuota. Los textos se conservan completos dentro del límite
de entrada; se usan grupos menores cuando no caben. Una respuesta cruzada entre
artículos o incompleta invalida todo el grupo, y los reintentos consumen el cupo.

El resumen separa llamadas, fichas evaluadas y publicables, tamaños de lote y
divisiones de recuperación. Esas medidas permiten comprobar la mejora real; no
se deduce el avance a partir de issues abiertas ni del máximo de cuatro artículos.
Publica sólo campos respaldados y seguros; la
generación por sí sola no autoriza publicar. Los fallos ordinarios conservan
reintentos acotados y los fallos de configuración requieren intervención técnica.
Las solicitudes de GitHub permiten reevaluar o corregir fichas; no se envía al
modelo el texto de los issues. Véase el [procedimiento Gemini](GEMINI-REVIEW.md).

## Presupuesto de minutos de Actions

El plan gratuito de GitHub incluye 2 000 minutos mensuales de Actions en el
repositorio privado; la automatización se dimensiona para no pasar del 80 %
(1 600 minutos). El modelo versionado está en `ops/actions-minutes-budget.json`
y `tests/test_actions_minutes_budget.py` lo contrasta con los horarios reales de
`vercel.json` y las plantillas privadas: dos ciclos diarios de extracción (01:07
y 13:07 UTC, con el respaldo de GitHub deduplicado por hora), dos recolecciones,
dos ingresos históricos y una consulta de estadísticas oficiales. El peor caso
mensual estimado es de unos 1 548 minutos, incluida una reserva manual de 60.

La medición se hizo con la API de jobs de Actions, sumando por job la duración
redondeada al minuto, que es como GitHub factura. Para volver a medir, consulte
los jobs de cada workflow en la ventana elegida y ajuste `per_run` y los
términos de `cycle`. Subir la frecuencia o el techo por ciclo exige actualizar
el modelo, mantener la prueba en verde y contar con el acuerdo del titular:
una cadencia horaria superaría la cuota incluida. El régimen anterior gastaba
unas 240 solicitudes diarias durante unos cinco días y después se detenía; la
cadencia actual reparte un volumen mensual semejante de forma continua, sin
cortarse a mitad de mes.

## Fichas de todas las noticias

Cada referencia tiene una estructura consultable. Los campos sin extracción o
validación pública permanecen pendientes; no se interpretan como ausencias en la
fuente. La generación privada utiliza `news_dossier` para todos los ámbitos y
conserva `record` sólo cuando hay además una propuesta de caso. Consulte
[NEWS-DOSSIERS.md](NEWS-DOSSIERS.md) para publicación automática de fichas parciales y correcciones humanas solicitadas.
