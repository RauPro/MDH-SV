# Ubicaciones automáticas sin revisar

El responsable autorizó una capa pública de departamentos automáticos sin revisión
humana previa. Esta excepción se limita a la ubicación y su procedencia: no aprueba
ni publica las fichas privadas, identidades, hechos, citas o cuerpos de artículos.

`apps/web/public/archive/locations.json` contiene el identificador y bibliografía
ya públicos, un departamento canónico, fechas y un localizador de párrafo con hash.
Todas las entradas conservan `review_status: automated_unreviewed` y
`human_reviewed: false`. La web, REST y MCP mantienen esos datos separados de la
ficha revisada. Una decisión humana siempre prevalece, incluso cuando protege la
ubicación o determina que no corresponde a El Salvador.

## Condiciones de extracción

El exportador exige un borrador procesado por Gemini, jurisdicción salvadoreña,
identidad y bibliografía coincidentes. Relee la fuente y comprueba el hash y el
párrafo citado. Acepta dos métodos, que no deben confundirse:

- `gemini_explicit_department`: departamento mencionado explícitamente.
- `gemini_municipality_reference`: municipio o distrito citado en contexto
  geográfico, vinculado al departamento mediante la [referencia oficial versionada](MUNICIPALITY-REFERENCE.md).
  `resolution` conserva la localidad, categoría de referencia, versión y enlace
  a la página del decreto. No se afirma que el departamento estuviera escrito
  en la noticia ni se infiere la fecha del hecho a partir de la reforma.

No usa títulos, URL ni etiquetas como ubicaciones. Los homónimos requieren una
pista departamental compatible en el mismo párrafo; las contradicciones, nombres
parciales, negaciones de ubicación y referencias de sedes/rutas quedan pendientes.
Una señal explícita de país extranjero incompatible también impide la asignación.
No se generan coordenadas personales ni se afirma una ubicación exacta.

Las alertas de privacidad no excluyen toda la ficha. Con la política de privacidad
por campo (`privacy_policy_version=2`), una localidad administrativa segura puede
proyectarse aunque otra casilla esté protegida. Un departamento o municipio
protegido no se reconstruye mediante otro campo. Los borradores antiguos con
alertas necesitan la nueva extracción; no basta con cambiarles una etiqueta.

El snapshot nuevo usa `schema_version=2`; los clientes también pueden leer la
versión anterior. Las ubicaciones v1 se comprueban de nuevo antes de reutilizar
su caché con las reglas actuales.

Ese control comprueba una **mención departamental**, no que sea el lugar de una
violación. La fuente puede contener errores o referencias secundarias. La capa
debe citarse como extracción automática y contrastarse antes de uso institucional.
Los resultados sin evidencia suficiente no se publican y siguen consultables
como noticias sin ubicación. Las fuentes restringidas no se eluden.

## Actualización periódica

En el repositorio privado, `Publicar ubicaciones automáticas` sigue las ejecuciones
exitosas de extracción y revisión, y admite inicio manual. Usa código público
fijado a un SHA revisado, lee los borradores privados y excluye candidatas que ya
no están pendientes o tienen una ficha pública revisada.

El lote comprueba hasta 20 fuentes. Reutiliza las comprobaciones positivas mientras
su borrador y evidencia permanezcan idénticos. Rota por hora el punto de inicio
entre candidatos elegibles, para que los rechazos antiguos no bloqueen el resto.
Con un conjunto estable, los siguientes horarios recorren todos los candidatos;
repetir una ejecución en la misma hora conserva el mismo punto de inicio.

Una deploy key permite escribir únicamente en el repositorio público MDH-SV;
GitHub no restringe esa clave por archivo. El workflow y las validaciones limitan
el commit a `apps/web/public/archive/locations.json`. La clave privada reside en
el secreto `NEWS_LOCATION_PUBLISH_KEY` del repositorio editorial; no se copia un
token personal ni la clave Gemini al repositorio público. No hay publicación de
fichas completas, aprobación editorial automática ni suma al catálogo de casos.

Para detener esta capa, desactive el workflow privado. Para retirar datos ya
publicados, vacíe `locations` en el snapshot mediante un cambio revisado y despliegue.
Revocar la deploy key y eliminar su secreto detiene futuras escrituras sin cambiar
el catálogo o el historial de revisiones.
