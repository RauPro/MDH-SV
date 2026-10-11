# Ingreso horario con presupuesto gratuito

El código separa el reloj de la extracción. Vercel Cron solicita `extract.yml`
al minuto 07 de cada hora. GitHub conserva el cron del minuto 23 como respaldo.
El runner admite una sola ejecución por hora UTC y reserva cuota antes de cada
petición a Gemini. La configuración del repositorio no acredita por sí sola que
las credenciales estén instaladas o que el cron esté activo en producción.

## Credenciales del disparador

La GitHub App debe ser privada, sin webhooks ni OAuth de usuario, instalada con
**Only select repositories** exclusivamente en `RauPro/MDH-SV-review`. Necesita
**Actions: read and write** y el permiso implícito **Metadata: read**. Puede iniciar
y administrar ejecuciones de Actions y leer sus logs y artefactos privados, que
pueden contener propuestas de fichas. No permite leer directamente archivos del
repositorio ni modificarlos. GitHub no ofrece este permiso para un solo workflow.
No necesita permisos de secretos, administración, contenidos ni workflows.
Su creación e instalación requieren autorización específica del propietario.
La revisión editorial rechaza emisores de tipo Bot; no sustituir esta App por
un token personal que pueda aparentar una aprobación humana.

Configurar únicamente en **Production** del proyecto Vercel `mdh-sv`:

| Variable | Contenido |
| --- | --- |
| `CRON_SECRET` | Secreto aleatorio de al menos 32 caracteres. |
| `MDH_DISPATCH_APP_ID` | ID de la GitHub App. |
| `MDH_DISPATCH_INSTALLATION_ID` | ID de su instalación seleccionada. |
| `MDH_DISPATCH_REPOSITORY_ID` | ID numérico de `RauPro/MDH-SV-review`. |
| `MDH_DISPATCH_PRIVATE_KEY` | PEM privado de la App, almacenado como secreto. |

La función sólo acepta GET autenticado, en producción, sin query ni cuerpo.
Genera un token de instalación efímero limitado a ese ID de repositorio y
`actions:write`, y sólo puede despachar `extract.yml` sobre `main`. Ni la clave
Gemini ni la deploy key del publicador de ubicaciones se copian a Vercel.
Revocar la instalación o la clave detiene este acceso.

## Configurar la cuota real

Comprobar el nivel gratuito y los límites **RPM**, **TPM de entrada** y **RPD**
del modelo `gemini-3.5-flash-lite` en AI Studio para el proyecto dueño de la clave.
No usar la cuota de otro modelo, una tabla antigua ni un panel vacío como dato.
Los límites son por proyecto, no por clave. El proyecto debe estar dedicado a
este procesador o su presupuesto debe descontar también el uso externo.

La variable de Actions privada `MDH_GEMINI_BUDGET_CONFIG` es un objeto JSON con:

| Campo | Regla |
| --- | --- |
| `project_id` | Proyecto verificado de Google. |
| `model` | `gemini-3.5-flash-lite`. |
| `rpm`, `rpd`, `input_tpm` | Enteros positivos verificados en AI Studio. |
| `daily_budget` | Presupuesto propio menor que `rpd`, con margen para otros usos. |
| `batch_ceiling` | Entero entre 1 y 50. |
| `verified_at` | Fecha y hora UTC de la comprobación, con zona horaria. |

No hay valores de producción predeterminados. El runner falla antes de llamar
al modelo cuando falta la configuración, no coincide el modelo o el registro
privado no es válido. Debe provisionarse `runtime/gemini-budget.json` mediante
`GeminiBudget.bootstrap(used_requests=...)`, usando la misma configuración y un
cliente autenticado del repositorio privado. Esta operación sólo crea un registro
ausente; no reinicia un contador existente.

Al instalar por primera vez, detener temporalmente la extracción anterior.
Contabilizar todas las solicitudes del día del Pacífico, incluidos reintentos
y otros clientes. Si no se conoce ese total, inicializar `used_requests` con
`daily_budget`: el procesador esperará al siguiente reinicio diario, sin suponer
un saldo disponible. No borrar ni reiniciar el registro para saltar una pausa.

## Cómo avanza la cola

El máximo de solicitudes de una ejecución es el menor de: límite solicitado,
`batch_ceiling`, saldo diario y saldo repartido entre las horas restantes del día
del Pacífico. Se redondea hacia arriba para aprovechar el saldo restante. Cada
reintento consume una solicitud de ese máximo. Las noticias nuevas, el backlog
y la recuperación de privacidad comparten el presupuesto.

Cada solicitud se registra antes del POST mediante la revisión SHA del archivo
privado. Una respuesta perdida, timeout o reserva caducada no devuelve cuota.
Los límites de RPM y TPM usan una ventana conservadora de 120 segundos para
cubrir la demora entre reserva y envío. La entrada se acota por bytes UTF-8 del
payload completo más margen; no es una medición exacta del tokenizador y puede
reducir el rendimiento. No se persisten texto, hashes de artículos ni claves.

Un 429 detiene llamadas hasta la siguiente medianoche de
`America/Los_Angeles`. La fecha de reinicio sigue el horario de verano del
Pacífico; no se codifica una hora fija de El Salvador. La cuota agotada no marca
la noticia como error ni modifica sus intentos o su borrador. Faltas de datos,
revisión humana y publicación conservan sus reglas anteriores.

`runtime/gemini-hourly.json` conserva hasta 72 horas con origen, hora solicitada,
hora de inicio, retraso y resultado. Todos los disparos comparten ese registro.
Si una ejecución se interrumpe, no se repite en la misma hora; puede continuar
en la siguiente. Una solicitud atrasada hasta dos horas consume la hora actual,
sin acumular lotes atrasados. Solicitudes más antiguas o futuras se rechazan.

## Instalación y comprobación

1. Ejecutar pruebas, revisar el commit público y fijar su SHA en los workflows
   privados. No entregar secretos a código de pull requests.
2. Autorizar e instalar la GitHub App. Verificar por API que selecciona solamente
   el repositorio privado y que el token no tiene permisos adicionales.
3. Configurar Vercel y la cuota privada; provisionar el contador con consumo
   previo explícito. Actualizar el workflow privado y desplegar producción.
4. Comprobar una invocación **Cron** en Vercel y su ejecución privada asociada.
   HTTP 202 significa solicitud aceptada; no demuestra una extracción terminada.
   Comparar `requested_hour`, `claimed_at`, `trigger_source` y el resultado del
   registro horario. Un segundo disparo debe registrar `hour_already_claimed`
   sin llamadas nuevas a Gemini.
5. Verificar borradores privados y la publicación independiente de ubicaciones.
   Una ficha sigue necesitando revisión humana antes de publicar su contenido.

Para detener llamadas nuevas, desactivar `extract.yml` en el repositorio privado.
Para detener también solicitudes del planificador, desactivar el cron en Vercel
o revocar la instalación de la App. Los datos ya procesados permanecen intactos.
Un proveedor indisponible puede impedir una hora de trabajo; no se promete una
hora exacta de ejecución ni una fecha de finalización del backlog.

Referencias oficiales: [límites de Gemini](https://ai.google.dev/gemini-api/docs/rate-limits),
[cron de Vercel](https://vercel.com/docs/cron-jobs),
[tokens de instalación de GitHub](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).
