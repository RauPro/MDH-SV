# Preparación reproducible y configuración de una instancia

## Checkout de desarrollo

Se soporta una aplicación desde un checkout Git completo en macOS o Linux,
arm64/x64. Instale Python 3.13, Node 22 (>=22.12) con npm y Git. `.python-version`
y `.nvmrc` declaran las líneas compatibles. No use un clon superficial: el
escaneo de secretos necesita toda la ascendencia publicable.

```sh
python3.13 scripts/bootstrap.py
.venv/bin/python scripts/check.py
```

La preparación instala `requirements.lock`, los módulos Python en modo editable,
los dos `package-lock.json` con `npm ci` y Gitleaks autenticado por SHA256. El
backend de construcción Python también está fijado. Repetir el comando conserva
`.env` y reutiliza una `.venv` compatible; rechaza una `.venv` incompatible o
simbólica. No crea credenciales. La primera instalación requiere Internet; la
comprobación ordinaria usa dependencias locales y pruebas con datos ficticios.
`check.py --audit` añade consultas al servicio de avisos de npm.

La comprobación ejecuta el escaneo completo del historial, auditoría de archivos,
coherencia de instancia, pruebas Python/API/web, lint y build. Regenera los
exports en un directorio temporal y los compara byte a byte sin cambiar los datos
del checkout. La CI conserva el escaneo previo a instalar dependencias del
proyecto y ejecuta también las auditorías npm.

El paquete editable es un mecanismo de importación de esta aplicación: un wheel
no incluye los catálogos ni constituye una distribución autónoma. Los datos por
defecto se resuelven desde el checkout del código, no desde el directorio de
trabajo. Para una exportación explícita:

```sh
.venv/bin/python -m mdh_pipeline.publication \
  --reviewed /ruta/absoluta/publications.json --output /ruta/absoluta/export
npm run dev:api
npm run dev
```

La API local escucha en `127.0.0.1:3001` (o `PORT`). Su paginación conserva esa
instancia local aunque se falsifique `Host` o `X-Forwarded-Host`. El frontend de
Vite usa su servidor de desarrollo; no requiere claves de Gemini ni de GitHub.

## Identidad y destinos

`instance.json` es configuración pública revisable, sin tokens. Copie el archivo
fuera del checkout, edite todos sus valores y aplíquelo con:

```sh
.venv/bin/python scripts/configure_instance.py --from /ruta/absoluta/fork.json
.venv/bin/python scripts/configure_instance.py --check
git diff
.venv/bin/python scripts/check.py
```

| Campo | Valor de la instancia |
| --- | --- |
| `schema_version` | `1` |
| `public_url` | Origen HTTPS propio, sin ruta, credenciales, query ni fragmento; HTTP sólo para loopback explícito |
| `public_repository` | `propietario/repositorio` público que contiene código y snapshots |
| `private_repository` | Cola privada distinta del repositorio público |
| `public_repository_id` | ID numérico real e inmutable del repositorio público en GitHub |
| `publisher_actor_id` | ID numérico del actor al que GitHub atribuye el push SSH del publicador |
| `mcp_allowed_hosts` | Lista exacta de `host[:puerto]`, incluyendo el de `public_url`; sin comodines |
| `editorial_email` | Correo autorizado para correcciones sensibles y retiradas |
| `reporting_responsible` | Usuario `@cuenta` o equipo `@organizacion/equipo` responsable, con acceso de escritura para CODEOWNERS |

Obtenga el ID del repositorio de `GET /repos/{owner}/{repo}` y el del actor de
su cuenta GitHub (`GET /users/{login}`); compruebe también la atribución de una
propuesta SSH de prueba. No reutilice los números de la instancia original. El
bot GitHub Actions conserva su identidad propia, separada del actor SSH.

El comando genera destinos literales en workflows y plantillas, formularios,
contactos de SECURITY y el propietario general de CODEOWNERS. Conserva las
excepciones exactas de archivos generados, los SHA de Actions, los SHA de código
aprobado y las restricciones de `main`/visibilidad. Revise el diff antes de
commitearlo. `--check` detecta divergencias. No activa GitHub, instala workflows
remotos ni verifica la recepción del correo. La evidencia histórica de P0 en la
documentación corresponde sólo a la instancia original.

Python y Node validan el mismo contrato y rechazan campos extra, claves
repetidas, archivos simbólicos, orígenes inseguros e identidades ambiguas. Leen
`instance.json` del checkout de código aprobado. `MDH_INSTANCE_FILE` permite una
ruta **absoluta** explícita a otro archivo igualmente confiable; no es una
entrada de peticiones ni de datos publicados. Una configuración inválida detiene
el proceso. La web sólo recibe la proyección pública necesaria; no incluye el
nombre de la cola, IDs internos ni variables privadas del entorno.

REST/OpenAPI y sus enlaces usan `public_url`; los formularios usan el repositorio
público configurado. MCP acepta sus hosts exactos y los dominios que Vercel inyecta en
`VERCEL_URL`/`VERCEL_PROJECT_PRODUCTION_URL`. Para otros dominios de preview,
añada un host concreto revisado; no derive destinos de cabeceras HTTP.
Vercel debe construir desde la raíz con Node 22 y `vercel.json`; los handlers
incluyen la configuración y los JSON públicos. No coloque secretos en el archivo.

## Activar los bots de un fork

Los jobs operativos requieren `MDH_AUTOMATION_ENABLED=true` en **cada repositorio**.
GitHub compara las cadenas sin distinguir mayúsculas; use `true` de forma
convencional. La variable ausente o `false` deja los jobs sin ejecutar, incluso si
se habilitan Actions en el fork. La CI de lectura sigue disponible sin secretos.
La variable no sustituye las comprobaciones de identidad, rama o privacidad.

1. Configure la instancia y pase `scripts/check.py`. Habilite el reporte privado
   de GitHub, autorice el correo y pruebe su recepción antes de anunciar esos
   canales. Revise [PRIVATE-REPORTING.md](PRIVATE-REPORTING.md).
2. Proteja `main` con PR, `verify` estricto, CODEOWNERS y administradores sujetos
   a las mismas reglas; sin bypass, force push ni borrado. Habilite la política
   de SHA de Actions y el permiso de creación de PR requerido por los bots.
3. Cree la cola privada e instale las nueve plantillas de `ops` según
   [PUBLICATION-SECURITY.md](PUBLICATION-SECURITY.md) y
   [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md). El historial público se instala en el
   repositorio público; no copie sus credenciales a la cola por conveniencia.
4. En **cada** plantilla privada, sustituya `MDH_PUBLIC_CODE_REF` por un SHA de
   40 caracteres revisado de **su fork**, posterior a su configuración. Confirme
   que todos los checkouts de código usan ese SHA y los datos usan la rama
   prevista. El configurador no promueve código automáticamente. Mantener los
   SHA originales en un fork ejecutaría código con destinos originales y debe
   corregirse antes de activar los jobs.
5. Configure sólo los secretos propios y de alcance mínimo descritos en las
   guías operativas. La cola debe seguir siendo privada: el cliente comprueba
   `full_name` y `private` mediante la API antes de escribir. El cron de Vercel
   usa una GitHub App limitada al ID real de esa cola; ver
   [HOURLY-INGESTION.md](HOURLY-INGESTION.md). No copie claves del upstream.
6. Active `MDH_AUTOMATION_ENABLED=true` cuando las comprobaciones anteriores
   estén resueltas. Pruebe una actualización de histórico limitada y una
   publicación con `validation_only=true`. Observe la PR, el escaneo, `verify`,
   la integración protegida y el despliegue. Después confirme una ejecución
   horaria real: un dispatch manual no demuestra por sí solo el horario.

Para pausar la automatización, establezca la variable en `false`. Un despacho
puede ser aceptado y terminar con jobs omitidos; compruebe los jobs y los datos
publicados, no sólo la aceptación HTTP. Los cambios de cola deben coordinar su
configuración, instalación de la App, código fijado y secretos antes de reactivar.

El fork ordinario hereda la raíz pública aprobada y su escaneo completo. Una
reescritura de historial requiere revisar deliberadamente
`.security/publication-history.json`; no quite el guard para hacer pasar el
bootstrap. Consulte [SECRET-HISTORY.md](SECRET-HISTORY.md).
