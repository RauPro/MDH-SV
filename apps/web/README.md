# Portal MDH-SV

Desde esta carpeta: `npm ci`, `npm test`, `npm run lint`, `npm run build`, `npm run dev`.

El frontend consulta `/data/catalog.json`. No requiere secretos ni API de IA. La fuente está en `../../data/reviewed/publications.json`; el generador Python produce JSON, CSV, GeoJSON, Parquet y manifest coherentes. `prebuild` falla ante divergencias o archivos públicos inesperados.

El despliegue usa el `vercel.json` de la raíz del repositorio. No cambies el Root Directory: debe ser `.` para comprobar la fuente canónica durante el build.
