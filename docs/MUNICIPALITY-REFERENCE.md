# Referencia de municipios y distritos de El Salvador

El catálogo versionado `sv-decree-762-2023-v1` contiene los 44 municipios y los
262 nombres históricos de municipios, ahora distritos, del artículo 1 del
Decreto Legislativo n.º 762. Cada entrada conserva el departamento, el municipio
que lo agrupa actualmente y la página de la fuente. No contiene coordenadas,
direcciones particulares, cantones ni colonias.

La fuente primaria es la [Ley Especial para la Reestructuración Municipal,
artículo 1, páginas 6–13](https://cuscatlansur.gob.sv/wp-content/uploads/2024/08/LEY-ESPECIAL-PARA-LA-REESTRUCTURACION-MUNICIPAL.pdf),
emitida por la Asamblea Legislativa y alojada por el municipio de Cuscatlán Sur.
Publicación: Diario Oficial n.º 110, tomo 439, 14 de junio de 2023. Se consultó
el 11 de octubre de 2026. El archivo JSON conserva el SHA-256 del PDF descargado,
su URL, fecha de publicación y fecha de consulta. La [comunicación oficial de la
Asamblea Legislativa](https://www.asamblea.gob.sv/node/12819) confirma las cifras
de 14 departamentos, 44 municipios y 262 distritos.

Los nombres de distrito se transcriben del listado del decreto. La comparación
ignora tildes y mayúsculas, por lo que acepta variantes ortográficas como
«Juayúa»/«Juayua» o «San Luis»/«San Luís» sin introducir nuevos lugares. Los
nombres de departamento usan la ortografía del esquema del monitor; los de los
44 municipios combinan ese departamento con el sufijo cardinal del decreto.

El archivo canónico es
`apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json`; se incluye
en la distribución de Python. El módulo `mdh_pipeline.municipalities` ofrece
`resolve_municipality(name, department_hint=None)`. Resuelve exclusivamente un
nombre administrativo completo ya extraído y respaldado por la noticia. No busca
nombres en titulares o texto libre, no usa coincidencias aproximadas y no añade
alias no documentados. El consumidor debe comprobar la cita y la referencia a
El Salvador: la unicidad dentro de este catálogo no demuestra el país del hecho.

San Fernando, San Isidro, San Lorenzo, El Carmen y El Rosario aparecen en más
de un departamento. Sin un departamento independiente y compatible, la función
devuelve `None`. También devuelve `None` si el departamento proporcionado no
coincide, incluso para un nombre único. Un departamento generado por el modelo
no basta para desambiguar: debe tener respaldo en la fuente.

La respuesta distingue `district` de `municipality` y devuelve el vínculo a la
página del decreto y la versión del catálogo. `district` identifica la entrada
histórica que hoy tiene esa categoría; **no afirma que el hecho sucedió después
de la reforma**. No se usa la fecha de publicación como fecha del hecho, ni se
renombra retrospectivamente el lugar descrito por una noticia antigua. La
relación municipal sirve para obtener el departamento, no para ubicar el hecho
en un punto específico ni afirmar una precisión que la noticia no aporta.

Las pruebas comprueban los 306 nombres, totales por departamento, las cinco
familias de homónimos, normalización y rechazo de direcciones, nombres parciales
y pistas contradictorias. Al actualizar la referencia, se debe verificar de
nuevo una fuente oficial, incrementar la versión, mantener la procedencia y
revisar los cambios territoriales antes de reconstruir la capa automática.
