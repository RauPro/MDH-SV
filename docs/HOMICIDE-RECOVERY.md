# Recuperación dirigida de noticias de homicidios de 2015

Esta búsqueda complementa el histórico ordinario con referencias candidatas para
revisión humana. No publica noticias, fichas, ubicaciones ni cifras de homicidios.
El año corresponde a la **fecha de publicación declarada por el periódico**, no
necesariamente al año del hecho. Un artículo puede cubrir varios hechos, otro país,
memoria histórica o el seguimiento de un caso anterior.

## Corte inicial de cobertura

Consulta del 13 de octubre de 2026, snapshot web de las 18:20 de El Salvador;
histórico del commit `e942920b61bc1067d53e3cd076dc54de182725ea`.

| Periódico | Localizadas de 2015 | Seleccionadas en la web |
| --- | ---: | ---: |
| Diario Co Latino | 590 | 288 |
| El Diario de Hoy, archivo | 392 | 12 |
| GatoEncerrado | 22 | 16 |
| Revista Factum | 4 | 0 |
| El Faro | 0 | 0 |
| Diario El Mundo | 0 | 0 |
| La Prensa Gráfica | 0 | 0 |
| **Total** | **1,008** | **316** |

Estos son conteos de referencias, no de homicidios ni de fichas extraídas. La
selección temática y la revisión de sus campos son etapas diferentes.

Co Latino, GatoEncerrado y Factum ya tenían los doce meses de 2015 enumerados en
sus API públicas: 12,048, 151 y 382 entradas consultadas respectivamente. Volver
a ejecutar el mismo filtro no amplía esa cobertura. El archivo EDH conservaba
5,829 URLs pendientes del recorrido general; Faro y Mundo no tenían un índice
completo de 2015 configurado y LPG mantenía restringido el acceso automatizado.

## Qué amplía la búsqueda

`mdh_pipeline.homicide_recovery` vuelve a examinar los metadatos de 2015 con un
filtro independiente: homicidios, asesinatos, feminicidios, masacres, matanzas,
acribillamientos, personas ultimadas y muertes con contexto violento. Las muertes
por accidentes, enfermedades o desastres no se seleccionan por la palabra
«muerto» aislada. Son indicios de descubrimiento, sujetos a errores y revisión;
no decisiones sobre pertinencia, jurisdicción o responsabilidad.

La búsqueda admite semillas descubiertas en fuentes públicas y recorre índices
permitidos. Cada artículo HTML debe proporcionar su fecha en metadatos del
editor. No se sustituye por `lastmod`, fecha de modificación, fragmentos del
buscador o números de la URL. Los IDs de EDH cercanos a artículos confirmados de
2015 sólo priorizan las solicitudes; una página con fecha de otro año se excluye.

El lector EDH respeta una declaración explícita de codificación. En páginas
antiguas sin ella, prefiere UTF-8 válido y después Windows-1252, observado en ese
archivo. Esto evita corromper acentos por una detección automática incorrecta.

## Ejecución y reanudación

```sh
python -m mdh_pipeline.homicide_recovery \
  --year 2015 \
  --data-dir data/history \
  --output-dir .local-quarantine/homicides-2015/recovery \
  --max-requests 450 \
  --source co-latino --source gatoencerrado --source factum \
  --source el-diario-de-hoy \
  --seed-file .local-quarantine/homicides-2015/recovery-seeds.json
```

El archivo opcional de semillas es una lista de objetos `source_id` y
`source_url`, mantenida fuera del repositorio público. El mismo directorio de
salida reanuda `state.json`; `candidates.json` contiene las referencias nuevas y
`report.json` conserva solicitudes, exclusiones, errores y trabajo pendiente.
Las páginas intermedias se guardan como metadatos, sin cuerpos periodísticos.
Cada invocación admite de 1 a 1,000 solicitudes, incluidas redirecciones, con
pausas y límites de bytes y tiempo. No consume Gemini.

Una denegación de acceso bloquea esa fuente para ese estado de recuperación;
reanudar no la elude ni cambia de ruta. Otros fallos tienen como máximo tres
intentos entre ejecuciones. La cobertura permanece declarada parcial, incluso
al acabar las páginas conocidas. La ausencia de resultados no acredita ausencia
de publicaciones o de hechos.

## Duplicados y frontera de revisión

Se compara contra todos los checkpoints existentes y el lote en preparación por
URL canónica, alias `www` permitidos, barra final e ID de WordPress del mismo medio.
No se fusionan artículos distintos por describir el mismo hecho. Coincidencias
de título y fecha o republicaciones requieren contraste antes de la incorporación.

Enviar el lote al repositorio privado autorizado:

```sh
python -m mdh_pipeline.homicide_recovery_queue \
  --candidates .local-quarantine/homicides-2015/recovery/candidates.json \
  --report .local-quarantine/homicides-2015/recovery/report.json
```

El proceso requiere `GITHUB_REPOSITORY=RauPro/MDH-SV-review` y un `GH_TOKEN`
autorizado para ese repositorio, sin escribir sus valores en archivos o logs.
Verifica que siga siendo privado. Guarda un manifiesto inmutable en
`discovery/homicides-2015/` y una issue con `discovery/pending`. Reintentar el mismo
lote recupera la misma issue. Antes de enviar un lote ampliado, excluir las
referencias ya en revisión: el guard rechaza solapamientos con otros lotes,
incluidos alias, sin modificar decisiones anteriores.

La issue utiliza un marcador diferente del ingreso de noticias. **No entra al
extractor ni a la selección automática pública.** Cerrar la issue o cambiar
etiquetas tampoco aprueba datos. La persona revisora debe contrastar fuentes,
fechas, relación con El Salvador, derechos humanos, duplicados y privacidad, y
registrar qué referencias acepta o rechaza.

Sólo después de esa revisión se prepara un PR que incorpore los metadatos aceptados
al histórico, preservando los checkpoints y las decisiones existentes. El código
de recolección puede desplegarse antes; el lote sin revisar no se agrega a
`data/history`, a las descargas públicas ni al manifiesto de selección.
Una vez incorporados los metadatos aceptados al histórico, las fichas de noticias
siguen el [flujo automático de evidencia y privacidad](NEWS-DOSSIERS.md), sin
aprobación humana previa de cada ficha. Esto no incorpora ni aprueba el lote de
descubrimiento pendiente: conserva su revisión independiente descrita arriba.
Nunca se identifica una ficha con una cantidad de víctimas.
