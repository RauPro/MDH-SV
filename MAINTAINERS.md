# Responsables y continuidad

Este archivo nombra a las personas responsables de esta instancia; cada fork lo
sustituye con sus propios responsables.

## Áreas

| Área | Responsable | Suplente | Alcance | Acceso necesario |
| --- | --- | --- | --- | --- |
| Código y CI | @RauPro | vacante | Pipeline, servidor, web, tests, `verify` | Escritura en el repositorio público |
| Datos y automatización pública | @RauPro | vacante | Snapshots generados, controlador de publicación, releases | Escritura en el repositorio público |
| Cola privada y Gemini | @RauPro | vacante | Cola editorial, presupuesto de Gemini, workflows privados | Acceso al repositorio privado y sus variables |
| Correcciones editoriales y retiradas | @RauPro | vacante | Solicitudes públicas y privadas, retiradas | Canal editorial privado |
| Seguridad | @RauPro | vacante | Avisos privados, credenciales, escáner | Reportes privados de vulnerabilidades |
| Infraestructura | @RauPro | vacante | Vercel, ajustes de GitHub, protección de ramas | Administración de ambos repositorios y del proyecto Vercel |
| Revisión temática del corpus editorial | vacante | vacante | Confirmar o corregir las etiquetas propuestas del corpus | Conocimiento en derechos humanos; ver docs/EDITORIAL-CORPUS.md |

## Objetivos de respuesta

El único compromiso vigente es la primera respuesta en **7 días naturales** en
los dos canales privados (correcciones editoriales y vulnerabilidades),
orientativo y sin carácter de SLA, según [SECURITY.md](SECURITY.md) y
[docs/PRIVATE-REPORTING.md](docs/PRIVATE-REPORTING.md). No hay otros plazos
prometidos.

## Continuidad cuando el responsable no está

- Los bots programados siguen operando dentro del presupuesto de minutos
  modelado en `ops/actions-minutes-budget.json`.
- Para pausar la automatización, poner `MDH_AUTOMATION_ENABLED=false` en cada
  repositorio (Settings → Secrets and variables → Actions → Variables), como
  describe [docs/FORKS.md](docs/FORKS.md); restaurar `true` para reanudar.
- Nunca sin el responsable:
  - integraciones a `main`;
  - cambios de credenciales o secretos;
  - promociones de workflows privados y cambios de SHA de código;
  - la clave de publicación;
  - retiradas editoriales y correcciones sensibles;
  - protección de ramas o reglas;
  - releases y tags;
  - ediciones de este archivo.

## Cómo llegar a ser suplente

1. Contribuir por las tareas iniciales y después revisar PRs.
2. Acordar el rol con el responsable.
3. Activar 2FA obligatorio.
4. Recibir el acceso mínimo necesario, otorgado por área; el acceso al
   repositorio privado aplica sólo al área de la cola privada.
5. Leer la documentación de incorporación: [CONTRIBUTING.md](CONTRIBUTING.md),
   [docs/OPERATIONS.md](docs/OPERATIONS.md),
   [docs/GITHUB-ACTIONS.md](docs/GITHUB-ACTIONS.md), [SECURITY.md](SECURITY.md),
   [docs/PRIVATE-REPORTING.md](docs/PRIVATE-REPORTING.md),
   [docs/EDITORIAL-CORPUS.md](docs/EDITORIAL-CORPUS.md) y
   [docs/RELEASES.md](docs/RELEASES.md).

## Riesgo de continuidad

Hoy una sola persona integra cambios, promueve los workflows privados, conserva
las credenciales y atiende los canales privados. Si esa persona falta, la
automatización sigue publicando pero ninguna revisión, promoción ni respuesta
privada puede sustituirla sin preparación previa.

## Tareas iniciales

- [#251](https://github.com/RauPro/MDH-SV/issues/251) — una fixture de contrato compartida para una regla de `parseNewsArchive` sin cobertura (good first issue).
- [#252](https://github.com/RauPro/MDH-SV/issues/252) — resumen en inglés del README (`README.en.md`) (good first issue).
- [#253](https://github.com/RauPro/MDH-SV/issues/253) — revisión temática de las 48 etiquetas propuestas del corpus editorial (help wanted).
- [#254](https://github.com/RauPro/MDH-SV/issues/254) — carga diferida del detalle de fichas en la web para reducir la carga inicial (help wanted).
- [#255](https://github.com/RauPro/MDH-SV/issues/255) — promover las actualizaciones mayores de Actions siguiendo docs/GITHUB-ACTIONS.md (help wanted).
- [#256](https://github.com/RauPro/MDH-SV/issues/256) — evaluar La Prensa Gráfica u otra fuente candidata contra docs/SOURCES.md (help wanted).
