# Seguridad

No publique claves, identidades de víctimas o pruebas privadas en issues. Para informar de un problema no sensible, describa únicamente el componente y el impacto general. El proyecto aún no dispone de un canal confidencial de recepción.

El portal público no recibe denuncias, no proporciona atención de emergencias y no promete anonimato de red. Las preferencias de tema se almacenan en el navegador; las consultas de búsqueda se realizan localmente. Los enlaces externos y el proveedor de alojamiento tienen políticas propias.

La publicación debe excluir `.env`, bases SQLite, candidatos de IA, datasets del prototipo y código legado. Ejecute `python scripts/audit_release.py` y `python scripts/publication_guard.py check` antes de cualquier push. El segundo control revisa también commits anteriores y archivos ya borrados. Instale el guard local siguiendo [el procedimiento del historial publicable](docs/SECRET-HISTORY.md).

El historial del prototipo contenía una credencial Google y no pertenece a la ascendencia pública permitida. Sus referencias retiradas sólo deben conservarse en recuperación privada, fuera de las referencias de Git que puedan publicarse. Un escaneo limpio y la eliminación de referencias no demuestran que Google haya revocado la clave; esa comprobación corresponde al titular. No publique la credencial ni la pruebe contra servicios para inferir su revocación.
