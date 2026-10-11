# Mapa departamental

Los contornos simplificados provienen del [SVG de El Salvador de Simplemaps](https://simplemaps.com/svg/country/sv), consultado el 9 de octubre de 2026. Los 14 contornos coinciden con los utilizados en el prototipo. La fuente se ha identificado y la falsa calificación de «oficial» se ha eliminado.

Se integran como parte del filtro interactivo MDH-SV, con etiquetas, recuentos, estilos y controles accesibles propios. El recurso no se ofrece como una colección de mapas sin modificaciones. Los contornos están sujetos a la [licencia de la biblioteca SVG de Simplemaps](https://simplemaps.com/resources/svg-license), no a la licencia MIT del código del proyecto. El comentario en `elSalvador.ts` conserva URL y hash del SVG consultado.

En la colección Reportes documentados, el mapa cuenta exclusivamente registros `incident` con departamento reconocido. Los registros sin departamento permanecen separados; no se asignan a un centroide ni a San Salvador. Informes de contexto y reportes colectivos se excluyen. Los conteos respetan búsqueda, temática, fechas y custodia, pero no el departamento seleccionado: así se mantienen comparables los departamentos al cambiar el filtro. Activar un departamento en el mapa selecciona el tipo de registro individual. Los contornos no representan ubicaciones personales ni una medición de prevalencia.

Los 14 botones nativos ofrecen la misma interacción que los contornos para teclado, lector de pantalla y pantallas pequeñas.

La colección Noticias utiliza `newsTerritory.ts` y `SalvadorMap unit="news"`. En la capa revisada requiere una ficha pública validada y vinculada a su referencia, revisión humana, jurisdicción `SV` y campo de departamento explícitamente reportado con un nombre canónico. Sus contadores respetan búsqueda, medio, ámbito estimado, tema y fechas de publicación; la selección departamental filtra la lista sin alterar la base de comparación del mapa. La capa revisada no deduce ubicaciones del título, URL ni `scope_hint`; la correspondencia municipal automática se documenta por separado. Los estados no localizables permanecen separados y consultables, y no se suman a los reportes individuales.

La capa automática autorizada lee únicamente `archive/locations.json` y se marca Sin revisar. Los campos revisados siempre prevalecen; las menciones automáticas no son localizaciones confirmadas de violaciones. La UI diferencia capas por rótulos, patrones y color, y permite filtrarlas. Cuando no puede cargar los snapshots necesarios muestra disponibilidad desconocida, no falsos ceros.

Las ubicaciones automáticas también admiten municipios y distritos citados:
`gemini_municipality_reference` conserva `resolution` y muestra la referencia
administrativa oficial, distinguiendo el término de la noticia del departamento
resuelto. El validador JS usa el mismo JSON canónico que Python; no hay una segunda
tabla manual ni geocodificación aproximada. Los datos geográficos protegidos y las
decisiones editoriales prevalecen.
