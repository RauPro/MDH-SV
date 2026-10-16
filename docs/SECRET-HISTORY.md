# Historial publicable y credencial del prototipo

## Alcance y estado

La historia pública empieza en `73f078b0ce688998fb0c096469c78b28ca338520`.
El commit antiguo `2b3d83334364eb4c45c9c8c86d09ff604e40cecc` pertenece al
prototipo y contiene una credencial Google. `.security/publication-history.json`
exige la raíz pública y rechaza cualquier tip que incorpore ese ancestro.
No se reescribe la historia pública limpia para retirar un objeto que no forma
parte de ella.

En el checkout auditado se verificó la restauración del bundle antes de retirar
las dos referencias antiguas. El `main` local se alineó con `origin/main`; ninguna
referencia local restante alcanza el commit del prototipo. Esto no certifica
otros clones ni sustituye la revocación de la credencial.

**P0.3 cerrado el 16 de octubre de 2026.** El titular confirmó primero que el
comparador local devolvió una coincidencia exacta con la credencial histórica y,
después, que eliminó esa clave y Google confirmó la eliminación. La clave se
utilizaba en Google Custom Search en el prototipo.

La evidencia de revocación es la confirmación explícita del titular recibida en
esa fecha; no se realizó una verificación independiente mediante la API o la
consola de Google. No se dispone de la hora exacta de eliminación ni del ID
administrativo del proveedor. La fecha anterior corresponde al registro del
cierre, no a una marca de tiempo emitida por Google. No se conserva el valor de
la clave en este registro.

El cierre reúne la revocación confirmada por el titular, la cuarentena local
recuperable y los controles de publicación integrados en las
[PR #71](https://github.com/RauPro/MDH-SV/pull/71) y
[#73](https://github.com/RauPro/MDH-SV/pull/73). La CI de #73 aprobó 2.512 pruebas
Python sin omisiones y 327 pruebas web. La publicación automática con escaneo
previo al push se comprobó mediante la
[PR #72](https://github.com/RauPro/MDH-SV/pull/72), integrada y desplegada.

Eliminar una rama, obtener cero alertas en GitHub o recibir un error de
autenticación no demuestra revocación. La credencial histórica no debe
restaurarse ni reutilizarse; nunca registrar su valor en issues, informes,
comandos compartidos o capturas.

GitHub ofrece escaneo nativo y protección de pushes para este repositorio. Son
una capa adicional, no un sustituto del control local y del cierre con el
proveedor. La consulta realizada durante P0.3 encontró ambos controles activados
y ninguna alerta de secretos en el repositorio público.

## Preparación local

Requiere Python 3.12+, Git y acceso a la release oficial para la primera
instalación. El scanner se ejecuta localmente, sin enviar código o secretos a un
servicio de análisis.

```sh
MDH_GITLEAKS_DIR="$(python3 scripts/install_gitleaks.py)"
export GITLEAKS_BIN="$MDH_GITLEAKS_DIR/gitleaks"
python3 scripts/install_publication_guard.py --gitleaks "$MDH_GITLEAKS_DIR/gitleaks"
python3 scripts/publication_guard.py --gitleaks "$MDH_GITLEAKS_DIR/gitleaks" check
```

Gitleaks 8.30.1 y sus SHA256 están fijados en
`.security/gitleaks-version.json`. El instalador comprueba el archivo antes de
extraer exclusivamente el binario; una reinstalación reconstruye el binario
desde el archivo autenticado. No instala programas globalmente. En un Python
local sin certificados raíz configurados, use `SSL_CERT_FILE` con un bundle CA
confiable ya instalado; nunca desactive la validación TLS.

El hook conserva una copia inmutable del guard y su configuración bajo
`.git/mdh-publication-guard/`, separada del checkout candidato. Por ello también
funciona al cambiar a una rama anterior. Ejecuta Python sin paquetes de usuario,
variables de configuración Python ni archivos de inicio del checkout. Un hook
existente ajeno o un `core.hooksPath` configurado requieren integración explícita;
el instalador no los sobrescribe. Repita la instalación después de aprobar una
actualización del guard o de sus reglas.

## Selección exacta del historial

`check` escanea HEAD completo por defecto, no sólo el diff reciente:

```sh
python3 scripts/publication_guard.py --gitleaks "$MDH_GITLEAKS_DIR/gitleaks" check --ref HEAD --ref refs/tags/v2.0.0
```

El ejemplo de tag sólo se usa si ese tag existe. No se aceptan referencias
inexistentes ni rangos que puedan omitir commits. El hook recibe los OID reales
que Git va a enviar y comprueba el lote entero. También escanea los nombres de
referencia, mensajes de commits y tags anotados. Una operación sólo de borrado
no necesita analizar objetos nuevos. Los destinos ajenos a `refs/heads/` y
`refs/tags/` se rechazan: no se publica con `--mirror`.

La variable exportada vale para esa terminal. El hook instalado conserva una
ruta absoluta y funciona también en sesiones posteriores; para comprobar el
historial manualmente en otra terminal, repita la preparación o indique
explícitamente la ruta del binario con `--gitleaks`.

Los snapshots superficiales, clones parciales, grafts, objetos faltantes y errores
del motor hacen fallar el control. Las replacement refs se ignoran. Los patches
incluyen la raíz, todos los padres de merges y archivos eliminados, sin ejecutar
textconv o external diff. El motor usa un repositorio bare temporal con objetos
de sólo lectura, sin heredar `.gitleaksignore`, configuración ni supresiones
inline del candidato. Un control adicional examina blobs binarios y rechaza
rutas privadas o bundles que se hayan añadido y borrado posteriormente.

La salida contiene únicamente tipo de objeto, OID, regla y línea; nunca valores,
fragmentos, mensajes o nombres sospechosos. Los reportes internos temporales
están restringidos y se eliminan al terminar. Códigos de salida: `0` comprobado,
`1` coincidencias y `2` error o historia no admitida. No tratar un error como un
resultado limpio.

## Precisión y límites

Se mantienen las reglas predeterminadas de Gitleaks y una regla Google explícita.
La regla genérica confunde algunos `source_record_key` con claves: la política
permite únicamente once identificadores editoriales comprobados, en el campo
exacto y cinco rutas concretas. No ignora commits, carpetas de tests ni nuevos
identificadores. Una coincidencia nueva necesita revisión y una excepción precisa
con pruebas negativas; no se añade una baseline global para silenciarla.

Ningún detector prueba ausencia absoluta de secretos. Valores cifrados,
comprimidos, ofuscados o formatos no reconocidos requieren revisión adicional.
Los archivos Git de recuperación (`.bundle`, `.pack`) y rutas privadas se
rechazan por política, incluso si su contenido comprimido no es reconocible.

Los hooks pueden omitirse deliberadamente con `--no-verify` y no se clonan con
Git. CI impide integrar un historial detectado, pero actúa después de subir la
rama: no evita por sí solo una primera exposición. De ahí la combinación de
guard local, revisión, escaneo nativo y protección de pushes en GitHub.

## Cuarentena local y recuperación

Antes de retirar las referencias antiguas, conservar un bundle local verificado,
con directorio `0700` y archivo `0600`, por ejemplo:
`mdh-quarantine/legacy-2b3d833.bundle` dentro del directorio común de Git
(`.git/` en un checkout ordinario). Queda fuera de las referencias publicables y
no puede indexarse, incluso al cambiar a una rama antigua. `.git/` está excluido
del despliegue. El guard también rechaza publicar una copia de ese bundle en el
árbol de trabajo, incluso si luego se borra del tip. No adjuntarlo a issues, releases ni artefactos públicos.

Después de verificar la recuperación, se puede alinear el `main` local con
`origin/main` y retirar `bug/production-readiness`, comprobando por compare-and-swap
que ambas aún apuntan al commit esperado. No purgar reflogs ni ejecutar una
recolección destructiva de objetos ajenos. Los objetos antiguos pueden permanecer
localmente para recuperación, pero ninguna referencia publicable debe alcanzarlos.
Si se necesita recuperar el prototipo, hacerlo en un repositorio privado aparte,
sin remoto público y conservando el estado de revocación de la clave.

## CI y publicación automática

El check obligatorio `verify` obtiene historia completa y ejecuta el control antes
de instalar dependencias del proyecto. Los publicadores también lo ejecutan tras
crear el commit y antes del primer push público. El runner aislado usa siempre
`monitor/scripts/publication_guard.py`, del SHA aprobado, sobre `--repo publication`.
Descarga todos los objetos y aplica sparse checkout local después: solicitar
sparse checkout directamente a `actions/checkout` produciría un clone parcial.

Tras integrar cambios del scanner, actualizar el SHA aprobado de la plantilla
privada y comprobar una publicación sin llamadas nuevas a IA. No sustituir el
control por un check sintético ni crear una excepción de protección para el bot.
