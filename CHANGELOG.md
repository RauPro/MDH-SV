# Registro de cambios

Todos los cambios destacables de este proyecto se documentan en este archivo.

El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-es/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/) para el
código. La política completa está en [docs/RELEASES.md](docs/RELEASES.md).

## [Sin publicar]

### Añadido

- `MAINTAINERS.md` con áreas, responsable, suplentes vacantes, continuidad y
  tareas iniciales.
- Comprobación periódica de deriva operativa y credenciales de solo lectura
  (`scripts/check_operations.py`, `ops/operations-expectations.json`).

### Cambiado

- Dependabot agrupa las actualizaciones menores y de parche por directorio.

### Corregido

- Las notas de release incluyen las definiciones de los enlaces que citan.

## [2.1.0] - 2026-10-31

### Añadido

- Casos vinculados a fuentes, colección editorial privada y panel ligero
  ([#10]).
- Recuperación de la plataforma de investigación con archivo, fichas y MCP
  remoto ([#12]).
- Procesamiento de noticias con Gemini en la cola editorial privada ([#14]).
- Archivo histórico desde 2010 con recolección reanudable, validado con el
  parser web antes de publicar ([#17], [#18]).
- Incorporación de El Faro, El Mundo y el archivo EDH con las restricciones de
  LPG visibles ([#19]).
- Ficha estructurada para cada noticia aunque falte información ([#20]).
- Noticias y fichas en la vista principal con mapa por departamentos y
  ubicaciones automáticas marcadas sin revisar ([#22], [#23]).
- Ubicación por municipio y protección de datos sensibles por campo ([#24]).
- Procesamiento del backlog con presupuesto persistente de Gemini, lotes
  adaptativos y validación por lotes con reparto de cuota ([#25], [#30],
  [#41]).
- Distinción entre pruebas manuales y el disparo programado de producción
  ([#26]).
- Selección temática: las noticias públicas exigen pertinencia en derechos
  humanos y vínculo con El Salvador ([#28]).
- Clasificación de todo el backlog con cobertura evaluada frente a pendiente
  ([#29]).
- Próxima actualización programada visible junto a la última ([#31]).
- Ubicaciones resueltas directamente desde la evidencia de la fuente, sin
  esperar las fichas ([#33]).
- Estadísticas oficiales de homicidios 2010–2026 con fuentes y cobertura
  explícita, consulta diaria con revisión privada previa y recuperación
  deduplicada de 2015 ([#35], [#36], [#37]).
- Publicación automática de fichas con evidencia, privacidad y presupuesto
  compartido; contrato de Gemini corregido con evaluaciones parciales seguras
  ([#39], [#40]).
- Evitación de altas históricas innecesarias antes de la clasificación ([#42]).
- Issues de noticias sincronizados con publicación y descarte, descartes
  prematuros evitados, cobertura de homicidios recuperada, evidencia negativa
  válida y reaperturas auditadas ([#44], [#45], [#46]).
- Recuperación de conflictos del archivo generado durante la publicación
  automática ([#47]).
- Canales privados para correcciones sensibles y vulnerabilidades ([#81]).
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
  automáticas únicamente mediante PRs protegidos con CI real, con promoción
  despachada explícitamente y comprobaciones nativas previas ([#48], [#51],
  [#52]).
- Actions fijadas por SHA con dependencias de workflow inmutables ([#86]).
- Importaciones aprobadas recuperables y exportaciones idempotentes ([#131]).
- Comprobaciones de calidad compartidas completas y auditorías de dependencias
  ([#221]).
- Contratos del pipeline y utilidades compartidas extraídos sin cambiar los
  snapshots ([#238]).
- Ciclo privado dos veces al día dentro de la cuota gratuita de Actions; techo
  de dieciséis solicitudes por ciclo separado del límite de incorporación a la
  cola ([#240], [#241]).

### Corregido

- Sitio y mapa: mapa de El Salvador restaurado con recuentos documentados
  ([#11]); detalle REST y errores JSON en el despliegue ([#13]); lectura de
  artículos con la plantilla Elementor antigua ([#21]); ubicaciones del mapa
  con corroboración trazable de la misma fuente ([#27]); menciones geográficas
  omitidas y claridad de las etapas de procesamiento ([#34]); el desglose
  revisado/automático se anuncia una sola vez ([#248]).
- Pipeline y Gemini: lectura de GatoEncerrado y normalización de campos
  desconocidos ([#15]); descarte de fechas y lugares incompatibles sin perder
  la ficha privada ([#16]); recuperación de lotes malformados y publicación de
  progreso parcial validado ([#32]); distinción entre fichas pendientes de
  extracción y generadas en revisión ([#38]); fechas RSS corregidas y una
  candidata inválida ya no detiene el lote ([#43]).
- Automatización: cierre de PRs de snapshots históricos sustituidos tras una
  integración confirmada ([#242]); el respaldo programado se descarta si llega
  fuera de su ventana (hora del cron, antes del minuto 45) sin reclamar franja
  ni llamar a Gemini ([#247]).

### Obsoleto

- Cobertura completa implícita en `GET /api/v1/news` sin el parámetro
  `coverage`: anunciado el 2026-11-01 con cabeceras `Deprecation`, `Sunset` y
  `Link`; el valor por defecto pasa a `summary` el 2027-02-01. La cobertura
  completa se conserva con `coverage=full` y en `/api/v1/news/coverage`
  ([#249]).

### Seguridad

- El historial publicable excluye secretos históricos en las referencias
  ([#71]); el escáner fijado exige pruebas reales de integración en Actions
  ([#73]); cierre documentado de la revocación de la clave histórica ([#78]).

## [2.0.0] - 2026-10-09

Edición pública inicial: registros bibliográficos públicos trazables a la
fuente con candidatos privados aislados; monitor institucional accesible con
fichas atribuidas y datos abiertos; verificación del límite de publicación y
despliegue seguro en Vercel; auditoría de producción documentada en
`docs/AUDIT-2026-10-09.md`.

[Sin publicar]: https://github.com/RauPro/MDH-SV/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/RauPro/MDH-SV/releases/tag/v2.1.0
[#10]: https://github.com/RauPro/MDH-SV/pull/10
[#11]: https://github.com/RauPro/MDH-SV/pull/11
[#12]: https://github.com/RauPro/MDH-SV/pull/12
[#13]: https://github.com/RauPro/MDH-SV/pull/13
[#14]: https://github.com/RauPro/MDH-SV/pull/14
[#15]: https://github.com/RauPro/MDH-SV/pull/15
[#16]: https://github.com/RauPro/MDH-SV/pull/16
[#17]: https://github.com/RauPro/MDH-SV/pull/17
[#18]: https://github.com/RauPro/MDH-SV/pull/18
[#19]: https://github.com/RauPro/MDH-SV/pull/19
[#20]: https://github.com/RauPro/MDH-SV/pull/20
[#21]: https://github.com/RauPro/MDH-SV/pull/21
[#22]: https://github.com/RauPro/MDH-SV/pull/22
[#23]: https://github.com/RauPro/MDH-SV/pull/23
[#24]: https://github.com/RauPro/MDH-SV/pull/24
[#25]: https://github.com/RauPro/MDH-SV/pull/25
[#26]: https://github.com/RauPro/MDH-SV/pull/26
[#27]: https://github.com/RauPro/MDH-SV/pull/27
[#28]: https://github.com/RauPro/MDH-SV/pull/28
[#29]: https://github.com/RauPro/MDH-SV/pull/29
[#30]: https://github.com/RauPro/MDH-SV/pull/30
[#31]: https://github.com/RauPro/MDH-SV/pull/31
[#32]: https://github.com/RauPro/MDH-SV/pull/32
[#33]: https://github.com/RauPro/MDH-SV/pull/33
[#34]: https://github.com/RauPro/MDH-SV/pull/34
[#35]: https://github.com/RauPro/MDH-SV/pull/35
[#36]: https://github.com/RauPro/MDH-SV/pull/36
[#37]: https://github.com/RauPro/MDH-SV/pull/37
[#38]: https://github.com/RauPro/MDH-SV/pull/38
[#39]: https://github.com/RauPro/MDH-SV/pull/39
[#40]: https://github.com/RauPro/MDH-SV/pull/40
[#41]: https://github.com/RauPro/MDH-SV/pull/41
[#42]: https://github.com/RauPro/MDH-SV/pull/42
[#43]: https://github.com/RauPro/MDH-SV/pull/43
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
