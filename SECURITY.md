# Seguridad

## Vulnerabilidades técnicas

Use [Reportar una vulnerabilidad de forma privada en GitHub](https://github.com/RauPro/MDH-SV/security/advisories/new). El reporte privado está habilitado; requiere una cuenta de GitHub. Responsable de recepción y seguimiento: [@RauPro](https://github.com/RauPro), propietario del repositorio.

Describa el componente, el impacto y los pasos para reproducirlo con datos ficticios. No incluya claves reales, identidades de víctimas ni documentos privados. Los reportes se tramitan en el aviso privado; los detalles que se publiquen después deben excluir datos sensibles. Los issues, comentarios de PR y discusiones ordinarias son públicos.

## Correcciones editoriales sensibles y retiradas

Escriba a [raul.guillen.eng@proton.me](mailto:raul.guillen.eng@proton.me) para correcciones sensibles o solicitudes de retirada de contenido publicado. Responsable de recepción y seguimiento: [@RauPro](https://github.com/RauPro). En el primer contacto incluya sólo la URL o el identificador público y una descripción mínima; no adjunte documentos de identidad, testimonios privados ni datos de terceras personas. El responsable solicitará por la misma vía sólo la información necesaria.

La recepción y la decisión son humanas. El correo no se incorpora automáticamente al pipeline, a Gemini ni a un issue público. Para errores no sensibles, utilice el formulario público con el identificador y una explicación general. No es necesario abrir una solicitud pública para obtener atención privada. Véase el [procedimiento de recepción y seguimiento](docs/PRIVATE-REPORTING.md).

Para ambos canales privados, el objetivo de primera respuesta es de **7 días naturales**. Es orientativo, no un SLA ni un plazo de resolución garantizado; no hay atención permanente. Los servicios de correo y GitHub tratan datos según sus propias políticas y estas vías no garantizan anonimato.

El portal público no recibe denuncias, no proporciona atención de emergencias y no promete anonimato de red. Las preferencias de tema se almacenan en el navegador; las consultas de búsqueda se realizan localmente. Los enlaces externos y el proveedor de alojamiento tienen políticas propias.

## Controles de publicación

La publicación debe excluir `.env`, bases SQLite, candidatos de IA, datasets del prototipo y código legado. Ejecute `python scripts/audit_release.py` y `python scripts/publication_guard.py check` antes de cualquier push. El segundo control revisa también commits anteriores y archivos ya borrados. Instale el guard local siguiendo [el procedimiento del historial publicable](docs/SECRET-HISTORY.md).

El historial del prototipo contenía una credencial Google y no pertenece a la ascendencia pública permitida. Sus referencias retiradas sólo deben conservarse en recuperación privada, fuera de las referencias de Git que puedan publicarse. El 16 de octubre de 2026 se registró la confirmación del titular: identificó la clave exacta, la eliminó y recibió confirmación de Google. [P0.3 queda cerrado con esa evidencia y los controles de publicación](docs/SECRET-HISTORY.md); no se afirma una verificación independiente con el proveedor. Un escaneo limpio y la eliminación de referencias no demuestran revocación. No restaure la credencial, publique su valor ni la pruebe contra servicios para inferir su estado.
