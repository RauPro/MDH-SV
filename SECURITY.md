# Seguridad

## Vulnerabilidades técnicas

Use [Reportar una vulnerabilidad de forma privada en GitHub](https://github.com/RauPro/MDH-SV/security/advisories/new). El reporte privado está habilitado; requiere una cuenta de GitHub. Responsable de recepción y seguimiento: [@RauPro](https://github.com/RauPro), propietario del repositorio.

Describa el componente, el impacto y los pasos para reproducirlo con datos ficticios. No incluya claves reales, identidades de víctimas ni documentos privados. Los reportes se tramitan en el aviso privado; los detalles que se publiquen después deben excluir datos sensibles. Los issues, comentarios de PR y discusiones ordinarias son públicos.

## Correcciones editoriales sensibles y retiradas

El canal separado para correcciones sensibles y solicitudes de retirada está pendiente de designación por el responsable. No use un issue público para aportar circunstancias privadas ni el formulario de vulnerabilidades para una corrección editorial ordinaria. Para errores no sensibles, describa únicamente el identificador de la ficha y el problema general en el formulario público.

El portal público no recibe denuncias, no proporciona atención de emergencias y no promete anonimato de red. Las preferencias de tema se almacenan en el navegador; las consultas de búsqueda se realizan localmente. Los enlaces externos y el proveedor de alojamiento tienen políticas propias.

## Controles de publicación

La publicación debe excluir `.env`, bases SQLite, candidatos de IA, datasets del prototipo y código legado. Ejecute `python scripts/audit_release.py` y `python scripts/publication_guard.py check` antes de cualquier push. El segundo control revisa también commits anteriores y archivos ya borrados. Instale el guard local siguiendo [el procedimiento del historial publicable](docs/SECRET-HISTORY.md).

El historial del prototipo contenía una credencial Google y no pertenece a la ascendencia pública permitida. Sus referencias retiradas sólo deben conservarse en recuperación privada, fuera de las referencias de Git que puedan publicarse. El 16 de octubre de 2026 se registró la confirmación del titular: identificó la clave exacta, la eliminó y recibió confirmación de Google. [P0.3 queda cerrado con esa evidencia y los controles de publicación](docs/SECRET-HISTORY.md); no se afirma una verificación independiente con el proveedor. Un escaneo limpio y la eliminación de referencias no demuestran revocación. No restaure la credencial, publique su valor ni la pruebe contra servicios para inferir su estado.
