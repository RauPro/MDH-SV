# Registro de cambios

Todos los cambios destacables de este proyecto se documentan en este archivo.

El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-es/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/) para el
código. La política completa está en [docs/RELEASES.md](docs/RELEASES.md).

## [Sin publicar]

## [2.1.0] - 2026-10-31

### Añadido

- Monitor público de noticias de derechos humanos: archivo seleccionado con
  evidencia por referencia, fichas estructuradas por noticia, mapa por
  departamentos, REST y MCP remoto ([#10]-[#24]).
- Cola editorial privada con Gemini: colección RSS, extracción de fichas,
  evaluación de campos contra la misma versión del artículo y presupuesto
  compartido persistente ([#14], [#25], [#30], [#39], [#41]).
- Archivo histórico recuperable desde 2010 con validación del parser web antes
  de publicar ([#17], [#18], [#19]).
- Estadísticas oficiales de homicidios 2010–2026 con fuentes y cobertura
  explícita y consulta diaria con revisión privada previa ([#35], [#36], [#37]).
- Fichas automáticas publicadas con evidencia, privacidad y presupuesto
  compartido; issues de noticias sincronizados con publicación y descarte
  ([#39], [#40], [#44], [#45], [#46], [#47]).
- Canales privados de correcciones sensibles y vulnerabilidades ([#81]).
- Preparación reproducible del checkout y configuración explícita de instancias
  para forks ([#100]).
- Contratos públicos compartidos y registro único de fuentes ([#112]).
- Corpus editorial de evaluación con criterios de aceptación condicionados a
  revisión humana ([#243]).
- Prueba offline del flujo completo privado→público y suite de navegador
  Playwright con comprobaciones de accesibilidad ([#245]).
- Respuestas de noticias acotadas: parámetro `coverage`, endpoint
  `/api/v1/news/coverage` y herramienta MCP `get_news_coverage` ([#249]).
- Guías de contribución y de agentes, código de conducta y formularios de
  issues ([#250]).

### Cambiado

- Automatización aislada del editor: `main` protegido y actualizaciones
  automáticas únicamente mediante PRs protegidos con CI real ([#48], [#51],
  [#52]).
- Actions fijadas por SHA con dependencias de workflow inmutables ([#86]).
- Importaciones aprobadas recuperables y exportaciones idempotentes ([#131]).
- Comprobaciones de calidad completas y auditorías de dependencias ([#221]).
- Contratos del pipeline y utilidades compartidas sin cambiar los snapshots
  ([#238]).
- Ciclo privado dos veces al día dentro de la cuota gratuita de Actions; techo
  de dieciséis solicitudes por ciclo separado del límite de cola ([#240],
  [#241]).
- El desglose revisado/automático del mapa se anuncia una sola vez ([#248]).

### Corregido

- Cierre de PRs de snapshots históricos sustituidos tras una integración
  confirmada ([#242]).
- El respaldo programado de GitHub se descarta si llega fuera de su ventana
  (hora del cron, antes del minuto 45) sin reclamar franja ni llamar a Gemini
  ([#247]).

### Obsoleto

- Cobertura completa implícita en `GET /api/v1/news` sin el parámetro
  `coverage`: anunciado el 2026-11-01 con cabeceras `Deprecation`, `Sunset` y
  `Link`; el valor por defecto pasa a `summary` el 2027-02-01. La cobertura
  completa se conserva con `coverage=full` y en `/api/v1/news/coverage`
  ([#249]).

### Seguridad

- El historial publicable excluye secretos históricos; escáner fijado con
  pruebas reales de integración en Actions y cierre documentado de P0.3
  ([#71], [#73], [#78]).

## [2.0.0] - 2026-10-09

Edición pública inicial del monitor de noticias de derechos humanos de El
Salvador: catálogo de registros bibliográficos, ficha estructurada por
documento, búsqueda y REST, con las políticas de publicación y evidencia.

[Sin publicar]: https://github.com/RauPro/MDH-SV/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/RauPro/MDH-SV/releases/tag/v2.1.0
[#10]: https://github.com/RauPro/MDH-SV/pull/10
[#14]: https://github.com/RauPro/MDH-SV/pull/14
[#17]: https://github.com/RauPro/MDH-SV/pull/17
[#18]: https://github.com/RauPro/MDH-SV/pull/18
[#19]: https://github.com/RauPro/MDH-SV/pull/19
[#24]: https://github.com/RauPro/MDH-SV/pull/24
[#25]: https://github.com/RauPro/MDH-SV/pull/25
[#30]: https://github.com/RauPro/MDH-SV/pull/30
[#35]: https://github.com/RauPro/MDH-SV/pull/35
[#36]: https://github.com/RauPro/MDH-SV/pull/36
[#37]: https://github.com/RauPro/MDH-SV/pull/37
[#39]: https://github.com/RauPro/MDH-SV/pull/39
[#40]: https://github.com/RauPro/MDH-SV/pull/40
[#41]: https://github.com/RauPro/MDH-SV/pull/41
[#44]: https://github.com/RauPro/MDH-SV/pull/44
[#45]: https://github.com/RauPro/MDH-SV/pull/45
[#46]: https://github.com/RauPro/MDH-SV/pull/46
[#47]: https://github.com/RauPro/MDH-SV/pull/47
[#48]: https://github.com/RauPro/MDH-SV/pull/48
[#51]: https://github.com/RauPro/MDH-SV/pull/51
[#52]: https://github.com/RauPro/MDH-SV/pull/52
[#71]: https://github.com/RauPro/MDH-SV/pull/71
[#73]: https://github.com/RauPro/MDH-SV/pull/73
[#78]: https://github.com/RauPro/MDH-SV/pull/78
[#81]: https://github.com/RauPro/MDH-SV/pull/81
[#86]: https://github.com/RauPro/MDH-SV/pull/86
[#100]: https://github.com/RauPro/MDH-SV/pull/100
[#112]: https://github.com/RauPro/MDH-SV/pull/112
[#131]: https://github.com/RauPro/MDH-SV/pull/131
[#221]: https://github.com/RauPro/MDH-SV/pull/221
[#238]: https://github.com/RauPro/MDH-SV/pull/238
[#240]: https://github.com/RauPro/MDH-SV/pull/240
[#241]: https://github.com/RauPro/MDH-SV/pull/241
[#242]: https://github.com/RauPro/MDH-SV/pull/242
[#243]: https://github.com/RauPro/MDH-SV/pull/243
[#245]: https://github.com/RauPro/MDH-SV/pull/245
[#247]: https://github.com/RauPro/MDH-SV/pull/247
[#248]: https://github.com/RauPro/MDH-SV/pull/248
[#249]: https://github.com/RauPro/MDH-SV/pull/249
[#250]: https://github.com/RauPro/MDH-SV/pull/250
