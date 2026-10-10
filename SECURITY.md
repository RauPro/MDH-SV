# Seguridad

No publique claves, identidades de víctimas o pruebas privadas en issues. Para informar de un problema no sensible, describa únicamente el componente y el impacto general. El proyecto aún no dispone de un canal confidencial de recepción.

El portal público no recibe denuncias, no proporciona atención de emergencias y no promete anonimato de red. Las preferencias de tema se almacenan en el navegador; las consultas de búsqueda se realizan localmente. Los enlaces externos y el proveedor de alojamiento tienen políticas propias.

La publicación debe excluir `.env`, bases SQLite, candidatos de IA, datasets del prototipo y código legado. Ejecute `python scripts/audit_release.py` y revise el diff antes de cualquier push. No restaure el historial anterior en la rama pública: contenía una credencial incrustada.
