# Cola editorial privada de MDH-SV

Este repositorio es privado. Todas las noticias recogidas tienen una estructura
de ficha, aunque falten datos, el ámbito territorial sea incierto o la noticia no
describa una violación. Una ficha no acredita los hechos ni se publica por sí sola.
No adjuntes cuerpos completos de artículos, fotografías, testimonios privados ni
documentos personales.

## Generación y estados

**Preparar fichas con Gemini** está programado cada hora al minuto 23. Recoge RSS,
incorpora hasta 25 referencias históricas y procesa hasta 20 noticias por lote.
Los RSS de Co Latino, GatoEncerrado y Factum y el ingreso histórico independiente
también tienen horarios cada seis horas. Comprueba en Actions los eventos
`schedule`: una ejecución manual exitosa no verifica el disparo del horario.

El histórico incluye por defecto todos los ámbitos territoriales y reserva turnos
para que las referencias internacionales o por determinar puedan avanzar. Las
fuentes bloqueadas no se eluden. Los fallos tienen diagnósticos y reintentos
acotados; la estructura pendiente no se confunde con una extracción terminada.

`drafts/<id>.json` contiene `news_dossier`. Puede incluir además `record`, una
propuesta de caso con requisitos más estrictos. Los datos desconocidos no se
rellenan mediante suposiciones:

- `pending`: campo no evaluado o pendiente de contraste.
- `not_reported`: dato expresamente no mencionado en el texto analizado.
- `withheld`: valor protegido y omitido.
- `not_applicable`: campo que no corresponde, con explicación.
- `reported`: valor atribuido a la fuente, con referencia de evidencia.

Los borradores antiguos con datos reutilizables se migran sin una llamada al
modelo. Sus vacíos no se transforman en ausencias declaradas. Los resultados que
antes no extrajeron campos pasan por el nuevo análisis. Git conserva las versiones
anteriores y se respetan las decisiones humanas existentes.

## Revisar una ficha de noticia

1. Abre la fuente original y `news_dossier` en el borrador. Contrasta la fecha,
   hechos, campos no mencionados, atribuciones, duplicados y privacidad. Corrige
   el JSON si corresponde; no necesitas inventar valores para completar campos.
2. En **Revisar noticia**, selecciona la issue, `approve` y
   `proposal_kind: news_dossier`. Confirma la revisión de privacidad y escribe tus
   comprobaciones en `review_notes` (mínimo 20 caracteres). Deja `record_json`
   vacío para usar el dossier guardado o proporciona el dossier corregido.
3. La propuesta validada queda en `approved-news/<id>.json` y en el artefacto
   privado `propuesta-publica`. La noticia pasa a `review/approved`. Esto todavía
   no publica contenido.
4. En el repositorio público del monitor, ejecuta:

```sh
python -m mdh_pipeline.review_import --kind news_dossier --candidate IDENTIFICADOR
```

El importador comprueba aprobación vigente, identidad y bibliografía, y prepara
los archivos revisables. Revisa el diff, crea commit/PR y espera CI antes de
publicar. La web y la API incorporarán únicamente los campos aprobados; comentarios,
diagnósticos y envoltorios privados no se exportan.

Una ficha parcial o internacional puede aprobarse como ficha de noticia. Un fallo
de lectura no puede aprobarse como lectura completada: primero consulta la fuente
y prepara o corrige su ficha. Para rechazar, elige `reject`; no se exige una ficha.

## Proponer un caso

`proposal_kind: case` conserva las reglas de jurisdicción, pertinencia y esquema
del catálogo. Usa `record` del borrador o un JSON manual completo. Las propuestas
quedan en `approved/<id>.json`; el importador anterior sin `--kind news_dossier`
incorpora casos. Las fichas de noticias nunca se suman automáticamente a ese catálogo.

## Código, credenciales y publicación

Los workflows usan un commit fijo del código público revisado. `GEMINI_API_KEY`
permanece en Actions Secrets y `GEMINI_MODEL` selecciona el modelo. No se guardan
claves en Issues, fichas, archivos públicos o Vercel. Ninguna llamada al modelo aprueba una ficha. El workflow Publicar ubicaciones
automáticas puede actualizar únicamente la proyección geográfica pública
`archive/locations.json`, marcada Sin revisar, mediante una deploy key del repositorio
público. No exporta cuerpos, otros campos, comentarios ni identidades personales.
Se ejecuta después de la extracción o revisión exitosa; las decisiones humanas
retiran la proyección automática. Consulte el protocolo de ubicaciones automáticas.

No hagas público este repositorio. Desactivar el workflow de Gemini detiene las
llamadas nuevas sin eliminar los borradores. No se notifican ni asignan terceras
personas automáticamente. [Protocolo de fichas](https://github.com/RauPro/MDH-SV/blob/main/docs/NEWS-DOSSIERS.md).
