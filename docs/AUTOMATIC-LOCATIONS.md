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

El exportador exige pertenencia al [archivo seleccionado](NEWS-SELECTION.md),
identidad y bibliografía coincidentes. Para una selección automática, el hash
geográfico también debe coincidir con la evidencia de esa selección vigente.
La comprobación geográfica es independiente de tener una ficha completa: relee
la fuente y busca evidencia administrativa literal, sin llamar a Gemini.
Los métodos conservan su procedencia:

- `gemini_explicit_department`: departamento mencionado explícitamente.
- `gemini_municipality_reference`: municipio o distrito citado en contexto
  geográfico, vinculado al departamento mediante la [referencia oficial versionada](MUNICIPALITY-REFERENCE.md).
  `resolution` conserva la localidad, categoría de referencia, versión y enlace
  a la página del decreto. No se afirma que el departamento estuviera escrito
  en la noticia ni se infiere la fecha del hecho a partir de la reforma.
- `gemini_department_corroborated`: el párrafo citado por Gemini sitúa una
  referencia geográfica en el departamento, y otro párrafo del mismo artículo
  lo identifica explícitamente como departamento. Se comprueba el mismo cuerpo
  y hash, con controles de negación, países incompatibles y sedes/rutas en ambos
  párrafos. Una mención aislada no basta. `corroboration` distingue el párrafo
  original del modelo del párrafo de apoyo encontrado por la regla automática;
  no se modifican las citas de la ficha privada ni se afirma corroboración
  independiente de los hechos.
- `source_explicit_department`: una regla conservadora identifica un departamento
  explícito en contexto geográfico dentro del texto comprobado.
- `source_municipality_reference`: la regla identifica un municipio o distrito
  en contexto geográfico y utiliza la misma referencia administrativa oficial.
  Estos dos métodos requieren `rule_version: 1`, no se atribuyen a Gemini ni
  necesitan una ficha previa. El método municipal conserva `resolution`.

No usa títulos, URL ni etiquetas como ubicaciones. Los homónimos requieren una
pista departamental compatible en el mismo párrafo; las contradicciones, nombres
parciales, negaciones de ubicación y referencias de sedes/rutas quedan pendientes.
Una señal explícita de país extranjero incompatible también impide la asignación.
No se generan coordenadas personales ni se afirma una ubicación exacta.
La comprobación directa tampoco transforma el nombre de una prisión, el lugar
de nacimiento de una persona o una mención ambigua en una ubicación. Si aparecen
varios departamentos sin poder establecer uno, deja la noticia sin resolver.

Las alertas de privacidad no excluyen toda la ficha. Con la política de privacidad
por campo (`privacy_policy_version=2`), una localidad administrativa segura puede
proyectarse aunque otra casilla esté protegida. Un departamento o municipio
protegido no se reconstruye mediante otro campo. Los borradores antiguos con
alertas necesitan la nueva extracción; no basta con cambiarles una etiqueta.

El snapshot nuevo usa `schema_version=4`; los clientes también pueden leer las
versiones 1, 2 y 3. Los métodos directos de fuente requieren la versión 4 y la
corroboración se admite desde la versión 3. Las ubicaciones
v1 se comprueban de nuevo antes de reutilizar su caché con las reglas actuales;
las comprobaciones positivas posteriores conservan su procedencia y fecha mientras
sigan coincidiendo con la selección vigente y los controles de privacidad.

Ese control comprueba una **mención departamental**, no que sea el lugar de una
violación. La fuente puede contener errores o referencias secundarias. La capa
debe citarse como extracción automática y contrastarse antes de uso institucional.
Los resultados sin evidencia suficiente no se publican y siguen consultables
como noticias sin ubicación. Las fuentes restringidas no se eluden.

## Actualización periódica

En el repositorio privado, `Publicar ubicaciones automáticas` sigue las ejecuciones
exitosas de extracción, reevaluación de pertinencia y revisión, y admite inicio manual.
También puede publicar resultados ya validados de un extractor fallido del mismo
repositorio privado y rama `main`, sin ocultar su error. No se aplica esa excepción
a revisiones humanas fallidas, cancelaciones o timeouts. Usa código público
fijado a un SHA revisado y conserva las exclusiones editoriales y fichas humanas.

El lote automático comprueba hasta 50 fuentes seleccionadas. Reutiliza las
comprobaciones positivas mientras la selección, la evidencia y la privacidad
permanezcan compatibles. Rota por hora el punto de inicio
entre candidatos elegibles, para que los rechazos antiguos no bloqueen el resto.
Con un conjunto estable, los siguientes horarios recorren todos los candidatos;
repetir una ejecución en la misma hora conserva el mismo punto de inicio.
El inicio manual permite comprobar 20 o 50 fuentes, incluso sin borrador completo.
Esta comprobación vuelve a leer las fuentes y publica únicamente la proyección
permitida; no solicita otra extracción a Gemini ni consume su cuota.

Una caché privada de Actions conserva únicamente recibos acotados de las lecturas
sin ubicación o inaccesibles. No guarda cuerpos, citas ni datos personales. Difiere
24 horas las comprobaciones sin resultado y siete días los accesos restringidos;
un cambio de hash de selección o de reglas invalida esa espera. Una caché corrupta
se descarta y se vuelven a comprobar las fuentes, sin relajar la validación pública.
Ni la caché ni sus motivos por noticia se incluyen en el commit público. El token
del publicador conserva sus permisos de sólo lectura sobre el repositorio privado.

Una deploy key permite escribir únicamente en el repositorio público MDH-SV;
GitHub no restringe esa clave por archivo. El workflow y las validaciones limitan
el commit al manifiesto `data/selection/news.json` y a los archivos públicos
`archive/news.json`, `archive/locations.json` y `archive/dossiers.json`. El último
sólo contiene fichas con aprobación humana previa. La clave privada reside en
el secreto `NEWS_LOCATION_PUBLISH_KEY` del repositorio editorial; no se copia un
token personal ni la clave Gemini al repositorio público. No hay publicación de
fichas completas, aprobación editorial automática ni suma al catálogo de casos.

Para detener esta capa, desactive el workflow privado. Para retirar datos ya
publicados, vacíe `locations` en el snapshot mediante un cambio revisado y despliegue.
Revocar la deploy key y eliminar su secreto detiene futuras escrituras sin cambiar
el catálogo o el historial de revisiones.
