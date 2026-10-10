# Mapa departamental

Los contornos simplificados provienen del [SVG de El Salvador de Simplemaps](https://simplemaps.com/svg/country/sv), consultado el 9 de octubre de 2026. Los 14 contornos coinciden con los utilizados en el prototipo. La fuente se ha identificado y la falsa calificación de «oficial» se ha eliminado.

Se integran como parte del filtro interactivo MDH-SV, con etiquetas, recuentos, estilos y controles accesibles propios. El recurso no se ofrece como una colección de mapas sin modificaciones. Los contornos están sujetos a la [licencia de la biblioteca SVG de Simplemaps](https://simplemaps.com/resources/svg-license), no a la licencia MIT del código del proyecto. El comentario en `elSalvador.ts` conserva URL y hash del SVG consultado.

El mapa cuenta exclusivamente registros `incident` con departamento reconocido. Los registros sin departamento permanecen separados; no se asignan a un centroide ni a San Salvador. Informes de contexto y reportes colectivos se excluyen. Los conteos respetan búsqueda, temática, fechas y custodia, pero no el departamento seleccionado: así se mantienen comparables los departamentos al cambiar el filtro. Activar un departamento en el mapa selecciona el tipo de registro individual. Los contornos no representan ubicaciones personales ni una medición de prevalencia.

Los 14 botones nativos ofrecen la misma interacción que los contornos para teclado, lector de pantalla y pantallas pequeñas.
