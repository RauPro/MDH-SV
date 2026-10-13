# Consulta periódica y revisión de estadísticas oficiales

Los dos workflows de esta carpeta se instalan **únicamente** en el repositorio
privado `RauPro/MDH-SV-review`. No son workflows activos del repositorio público.
Antes de instalarlos, sustituir `MDH_PUBLIC_CODE_REF` por el SHA completo de un
commit público revisado y con CI aprobado. El código permanece fijado; los datos
de referencia se leen de `main` del monitor en cada ejecución.

## Consulta diaria

Instalar `monitor.yml` como `.github/workflows/official-statistics-monitor.yml`.
**Consultar estadísticas oficiales** se programa a las **06:37 de El Salvador**
(12:37 UTC), una vez al día, y admite ejecución manual. GitHub puede retrasar los
cron; una ejecución manual comprueba el procesamiento, no la puntualidad del cron.

El proceso compara la tabla nacional de FGR por contenido estadístico, no por
el contador de visitas de la página. Conserva ceros publicados y celdas vacías,
detecta revisiones de cifras anteriores y exige coherencia de meses, períodos,
etiquetas y totales. Puede preparar `proposed-homicides.json`, pero **no modifica
el conjunto publicado**. El contrato compartido de la web valida la propuesta
antes de encolarla.

El paso a otro mes o año también puede generar una propuesta de cobertura para
marcar períodos nuevos sin datos y actualizar qué meses quedan fuera del corte.
No cambia la fecha de la publicación a diario sin una diferencia estadística o
de cobertura; todos esos cambios siguen requiriendo revisión.

También comprueba los documentos IML/UNODC incorporados y vigila catálogos
oficiales para detectar nuevas publicaciones. Un PDF/XLSX cambiado o un documento
nuevo requiere lectura humana: su título o hash no se transforma en cifras.
El inventario inicial evita encolar todos los documentos históricos del catálogo.
Los fallos de acceso se conservan como alertas; no eliminan datos ni se traducen
en ceros. El workflow termina con fallo cuando la consulta encuentra errores,
después de guardar el informe y la cola, para no presentar una consulta incompleta
como verificación satisfactoria de todas las fuentes.

La ejecución conserva `report.json`, `review.md`, `source-state.json` y, cuando
corresponde, una propuesta en el artefacto privado **estadisticas-oficiales-consulta**
durante 30 días. Los cambios y decisiones persistidos en `official-statistics/`
del repositorio privado no dependen de la caducidad del artefacto. La cola usa una
identidad estable por revisión; las consultas repetidas no crean otra issue ni
sobrescriben una decisión humana por cambiar la hora o el HTML decorativo.

## Revisión humana

Instalar `review.yml` como `.github/workflows/official-statistics-review.yml`.

1. Abrir la issue privada y la propuesta enlazada. Contrastar la fuente original,
   localizadores, período, unidad, cambios de metodología y datos ausentes.
2. Ejecutar **Revisar estadísticas oficiales** con el número de issue, identificador
   completo de revisión y decisión. Para aprobar, confirmar `confirm_source` y
   describir las comprobaciones en `review_notes` (mínimo 20 caracteres).
3. El proceso exige identidad humana con acceso de escritura, la revisión exacta,
   el hash de la propuesta y un conjunto público de referencia que siga vigente.
   Valida de nuevo la propuesta con el contrato de producción. La aprobación
   sólo abarca las cifras propuestas; no certifica las fuentes que siguen inaccesibles.
4. Una aprobación genera el artefacto privado **estadisticas-oficiales-aprobadas**.
   **Aprobar no despliega.** Para publicar, revisar la decisión vigente y su hash,
   copiar únicamente el conjunto aprobado a `data/official-statistics/homicides.json`,
   regenerar las descargas, revisar notas metodológicas y pruebas de regresión,
   y tramitar un PR con CI. Si el conjunto público cambió desde la propuesta,
   consultar y revisar de nuevo; no reemplazar cambios posteriores.

Los avisos de documentos nuevos no contienen necesariamente una propuesta numérica.
Primero deben contrastarse y transcribirse con su metodología. No existe una
aprobación por etiqueta, comentario, modelo de IA o resultado de un cron.

## Límites de acceso y aislamiento

Sólo se usa `GITHUB_TOKEN` efímero del repositorio privado, con `contents:write`
e `issues:write`. La consulta de las fuentes no recibe ese token. No se usan Gemini,
PAT nuevos, deploy keys, claves de Vercel ni cambios en permisos del monitor público.
Los checkouts no persisten credenciales. Los workflows no tienen pasos para hacer
push público y no están entre los disparadores del publicador de noticias.
No se asignan revisores ni se mencionan personas automáticamente.

Ambos procesos usan el mismo grupo de concurrencia y preservan decisiones y
propuestas existentes. Una descarga bloqueada no se elude mediante otra ruta.
Las respuestas de las fuentes son datos no confiables; no ejecutan código ni
eligen destinos de escritura.
