# Instrucciones para agentes de codificación

Guía corta para agentes de IA que editan este repositorio. Los detalles y el
mapa completo están en [CONTRIBUTING.md](CONTRIBUTING.md); las reglas de datos
y de contratos allí también aplican aquí.

## Comandos

```sh
python3.13 scripts/bootstrap.py              # preparar .venv, dependencias fijadas, Gitleaks y Chromium
.venv/bin/python -m pytest <tests/…> -q      # pruebas acotadas Python del área
npm run test:api                             # API pública y contratos OpenAPI
npm --prefix apps/web test                   # Vitest
npm --prefix apps/web run test:browser       # Playwright sobre dist/
.venv/bin/python scripts/check.py            # comprobación completa (--audit con seguridad)
```

## Nunca hacer

- Fabricar registros, etiquetas, fechas, lugares o fuentes.
- Relajar validadores o `additionalProperties`, o convertir una fixture a
  válida para que una prueba pase.
- Editar a mano los snapshots generados (las rutas de `.github/CODEOWNERS`).
- Añadir acceso de red a las pruebas.
- Hacer commit de secretos, `.env`, cuerpos de artículos o datos privados.
- Quitar los SHA de las Actions o ampliar permisos de workflows.
- Añadir servicios de pago.
- Tratar las plantillas de `ops/` como workflows activos; la promoción sigue
  [docs/GITHUB-ACTIONS.md](docs/GITHUB-ACTIONS.md).
- Atribuir revisión humana a salidas automáticas.
- Marcar las propuestas del corpus editorial como confirmadas.

## Idiomas

Documentación en español; código, comentarios, commits y títulos de PR en
inglés.
