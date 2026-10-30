# Calidad y dependencias

## Base de lint

La base comprobada no tiene diagnósticos pendientes ni un archivo de excepciones
históricas. Ruff usa la configuración explícita del repositorio; no depende de
las preferencias globales del mantenedor:

```sh
.venv/bin/python -m ruff check --config pyproject.toml .
npm run lint
npm --prefix apps/web run lint
```

Python: reglas `E4`, `E9`, `F` y `B023` sobre módulos, scripts y pruebas. Detectan
imports inválidos, errores de sintaxis, nombres sin definir, variables sin uso y
cierres sobre variables de bucle. No se impone una reformateación masiva ni reglas
adicionales de estilo en este punto del roadmap. Añadir familias de reglas exige
revisar y corregir sus diagnósticos en el mismo cambio.

El lint raíz cubre el JavaScript activo de API, servidor, contratos compartidos,
scripts y pruebas, incluidos nuevos archivos bajo la raíz. `apps/web` usa su propio
lint de React/TypeScript; `legacy` contiene la web archivada y queda fuera del lint
JS activo. Ambos comandos rechazan advertencias. Las únicas supresiones nuevas son
locales y explicadas: expresiones regulares que rechazan caracteres de control
intencionadamente en la frontera de datos públicos.
