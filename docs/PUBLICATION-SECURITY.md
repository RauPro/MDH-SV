# Publicación aislada y protección de main

Código, datos revisados y actualizaciones automáticas se integran mediante pull
requests. Todos necesitan el check `verify`. Ningún bot tiene una excepción
general para escribir en `main`.

## Separación de permisos

La plantilla privada `publish-locations.yml` separa dos runners:

- `publish` prepara datos usando exclusivamente `MDH_PUBLIC_CODE_REF`, un SHA
  completo revisado. Puede leer borradores y evaluar con Gemini dentro del
  presupuesto existente. El checkout público contiene sólo datos y no recibe
  la clave de escritura. `publication_snapshot check-inputs` rechaza symlinks
  y archivos ejecutables antes de leerlos. Python se ejecuta fuera del checkout
  de datos; los scripts Node aprobados reciben `--root`.
- `propose` recibe exclusivamente los seis JSON enumerados en
  `mdh_pipeline.publication_snapshot.PUBLIC_FILES` y su manifest. No tiene la
  cola privada, Gemini ni un token con permiso para leer ese repositorio. Valida
  rutas, base Git, tamaños y hashes antes de aplicar y comprobar el snapshot.
  La deploy key sólo aparece aquí y permite proponer una rama
  `chore/automated-news-<run>-<attempt>`; nunca se hace push a `main`.

El artefacto se descarga desde el mismo run y caduca al día. No incluye borradores,
exclusiones privadas, cachés ni respuestas del proveedor. El manifest comprueba
integridad del traslado; no certifica hechos ni sustituye la validación semántica.

El job final informa fallos de evaluación aunque se haya propuesto un lote previo
válido. `validation_only: true` comprueba recibos existentes sin nuevas llamadas
a Gemini ni comprobaciones geográficas de fuentes; no publica campos pendientes.

## PR y CI

Los bots públicos crean ramas y PR con el token efímero y despachan `ci.yml`
explícitamente. No dependen de que un push con `GITHUB_TOKEN` dispare otro
workflow. El push SSH privado sí dispara CI público. Verificación tiene sólo
permisos de lectura y no recibe secretos.

El ensayo real confirmó que terminar un CI despachado con `GITHUB_TOKEN` no
garantiza un evento `workflow_run`. Por eso el productor solicita el ID exacto
con [`return_run_details`](https://github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids/),
verifica repositorio, workflow, rama, SHA e identidad del bot y despacha también
`publication-pr.yml` sobre `main`, con su `ci_run_id`. El
controlador espera un resultado satisfactorio dentro de un plazo acotado y
reutiliza todas las validaciones de origen, cambios permitidos y SHA. La espera
no concede permisos de escritura al código candidato. Una actualización de rama
repite ambos despachos; el evento `workflow_run` se conserva para pushes SSH.

GitHub no considera los checks de `workflow_dispatch` como checks obligatorios
para integrar un PR. Para esos productores, después de comprobar el árbol
completo de cambios, el autor, las referencias y que la rama está actualizada,
el controlador busca los workflows nativos `pull_request` del mismo PR y SHA.
Sólo permite `ci.yml` y `deploy_web.yml`, ambos con permisos de lectura. Si están
en `action_required`, solicita su ejecución mediante el endpoint de aprobación
de workflows, una sola vez por ejecución. Espera el éxito del CI nativo y del
comprobador de configuración si existe, vuelve a comprobar las referencias y
solicita la fusión normal. No añade reviews de aprobación al PR ni publica
checks sintéticos. Un error de permisos al aprobar o un resultado fallido detiene
la publicación; nunca cambia las reglas de protección. El CI de un push SSH ya
es elegible y conserva su ruta verificada de publicación.

Cada espera de CI está limitada a 18 minutos; el job del controlador permite
40 minutos para cubrir la validación despachada y los checks nativos. La cola
conserva los controladores pendientes con `queue: max`.

`publication-pr.yml` ejecuta código de confianza tras CI. Nunca descarga ni
ejecuta código del PR. Su controlador verifica repositorio, workflow, actor,
rama, SHA y árboles Git completos. Sólo acepta JSON regulares dentro de la
lista del productor; rechaza código, symlinks, eliminaciones y árboles truncados.

Las PR existentes deben pertenecer a `github-actions[bot]`. El origen de noticias
debe ser un push del propietario que controla la deploy key (`RauPro`, ID
37725305), comprobado en la ejecución pública de CI. El autor de un commit o
una etiqueta no conceden confianza. Cambiar propietario o autenticación exige
revisar y probar este contrato.

El merge es squash, sobre el SHA verificado, sin bypass. Si `main` avanzó, se
actualiza la rama y se repite CI; un conflicto queda visible y no autoriza
sobrescribir JSON. Otra ejecución puede preparar una propuesta nueva. Proponer
un snapshot no equivale a integrarlo ni desplegarlo. Tras un merge confirmado
de historia, el controlador cierra como sustituidas las PR abiertas de
instantáneas de bibliografía estrictamente anteriores del mismo bot, con
comentario y sin borrar ramas; nunca toca PR de noticias ni de excepciones,
porque su contenido no se regenera. Los cierres son de mejor esfuerzo: un fallo
sólo emite una advertencia, nunca convierte el merge en error.

La conciliación privada de issues consulta el sitio desplegado. Puede conservar
una ficha pendiente hasta que se integre y despliegue el PR; una conciliación
posterior la reconocerá sin volver a evaluar con Gemini.

## Política de main

`.github/main-protection.json` describe la configuración que debe aplicarse a
`PUT /repos/RauPro/MDH-SV/branches/main/protection` y verificarse mediante GET.
Versionar el archivo no cambia GitHub por sí solo.

- PR obligatorio, historial lineal y conversaciones resueltas.
- Check `verify` actualizado con `main`, emitido por GitHub Actions (App 15368).
- Revisión de CODEOWNERS; las aprobaciones obsoletas se descartan.
- Reglas aplicadas a administradores; sin bypass para bots.
- Force push y borrado de `main` deshabilitados.

Las aprobaciones globales son **cero intencionalmente**. CODEOWNERS asigna todo a
`@RauPro`, salvo catorce rutas JSON generadas exactas sin dueño. Los datos pueden
integrarse tras CI; código, workflows, dependencias, política, fuentes revisadas
y estadísticas requieren al propietario. Nunca ampliar las excepciones a
directorios o comodines. Los tests de protección fijan este límite.

Actions debe tener habilitada la creación de PR. Los workflows no emiten
aprobaciones; su token no sustituye al CODEOWNER. No se necesita habilitar
auto-merge general: el controlador solicita una fusión normal bajo las reglas.

## Activación y recuperación

1. Integrar código, tests, controlador y plantillas revisadas.
2. Instalar la plantilla privada sustituyendo ambos `MDH_PUBLIC_CODE_REF` por el
   SHA completo integrado. Conservar la versión anterior para comparar ajustes.
3. Esperar a que terminen las publicaciones antiguas antes de activar protección.
   Habilitar creación de PR, aplicar la política y verificarla remotamente.
4. Ejecutar el publicador con `validation_only: true` y un refresco público
   acotado. Seguir propuesta, CI, controlador, merge y despliegue Vercel. Una
   ejecución sin cambios es válida, pero no demuestra por sí sola el merge.
5. Comprobar los rechazos de paths ajenos y SHA obsoletos con los tests locales.

Ante un fallo, conservar protección y evidencias. Resolver el conflicto o preparar
otro snapshot; no añadir bypass ni restaurar pushes directos. Revisar el PR y
Actions antes de afirmar que una actualización está publicada.

Si CI ya terminó y faltó el encadenamiento, puede ejecutarse manualmente
`Merge verified public snapshots` desde `main` indicando el ID de la ejecución
de CI. Este disparo no concede aprobación: el controlador vuelve a comprobar
que pertenece al productor permitido, pasó sobre el SHA actual y sólo modifica
los JSON autorizados. Una ejecución ajena, fallida u obsoleta no se integra.
