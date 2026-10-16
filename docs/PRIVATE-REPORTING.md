# Recepción privada y seguimiento de reportes

## Canales y responsable

| Solicitud | Destino | Tratamiento |
| --- | --- | --- |
| Vulnerabilidad del código o infraestructura | [Reporte privado de GitHub](https://github.com/RauPro/MDH-SV/security/advisories/new) | Requiere cuenta; aviso privado de seguridad |
| Corrección editorial sensible o retirada | [raul.guillen.eng@proton.me](mailto:raul.guillen.eng@proton.me) | Correo privado y revisión humana; no requiere issue público |
| Error no sensible de una noticia, registro o sitio | [Formularios públicos](https://github.com/RauPro/MDH-SV/issues/new/choose) | El contenido y los adjuntos del issue son públicos |

El responsable de recepción, seguimiento y decisión es [@RauPro](https://github.com/RauPro).
El correo editorial fue autorizado por el titular para su publicación. El objetivo
de primera respuesta en ambos canales privados es de **7 días naturales**. Es
orientativo, no un SLA ni una promesa de resolución en ese plazo. No hay atención
permanente, atención de emergencias ni recepción de denuncias. Correo y GitHub
están sujetos a sus propias políticas y no garantizan anonimato.

## Primer contacto y tratamiento

Para una corrección editorial, enviar sólo la URL o el identificador público y
una explicación mínima. No pedir al remitente que abra un issue, se identifique
públicamente o adjunte documentos personales, testimonios o datos de terceros.
Para una vulnerabilidad, pedir componente, impacto y reproducción con datos
ficticios; nunca claves reales o información de personas protegidas.

El responsable debe:

1. Revisar ambos canales y responder por la misma vía privada. Confirmar el
   alcance y solicitar sólo la información adicional necesaria.
2. Valorar el riesgo de exposición y la corrección o retirada que corresponda.
   Una petición privada no aprueba por sí sola nuevos campos ni hechos.
3. Mantener mensajes, adjuntos, identidad del remitente y motivos sensibles fuera
   de Git, issues públicos, prompts, Gemini y logs. No existe integración de
   este buzón con la cola de procesamiento o con el publicador.
4. Aplicar la decisión con los permisos y comprobaciones habituales. Para
   noticias, crear el registro público mínimo a nombre del mantenedor con el ID
   y la decisión, sin revelar el origen privado ni sus motivos. Conservar el
   encabezado `Identificador de la noticia` y la etiqueta `news-correction` si
   se utiliza el workflow de excepciones. Sólo un mantenedor autorizado puede
   aplicar `news/withdraw`, `news/restore` o `news/recheck`.
5. Verificar los snapshots y el despliegue, y comunicar el resultado por el
   canal privado. Una retirada del sitio no elimina las copias de terceros.
   Conservar sólo la información privada necesaria para el seguimiento y
   eliminar copias y adjuntos que ya no sean necesarios.

Las correcciones humanas siguen [NEWS-DOSSIERS.md](NEWS-DOSSIERS.md) y conservan
su precedencia sobre resultados automáticos. El catálogo documental mantiene su
revisión y regeneración propias. No se modifica el presupuesto de Gemini ni se
reinician intentos al recibir un correo.

## Comprobación operativa de P0.4

El criterio del roadmap es que un reporte ficticio llegue por una vía privada y
que la política y los formularios indiquen el destino correcto.

- El reporte privado de vulnerabilidades se habilitó en GitHub y se confirmó
  mediante lectura de su configuración.
- El ensayo técnico del 16 de octubre de 2026 usó la ruta de borrador privado
  documentada por GitHub para el administrador: lectura autenticada correcta,
  respuesta anónima 404 y cierre sin publicación, CVE ni fork. No constituye
  una prueba de envío desde una cuenta externa.
- La publicación de prueba sin nuevas llamadas a Gemini pasó el escaneo previo
  al push y la CI, se integró automáticamente en la
  [PR #84](https://github.com/RauPro/MDH-SV/pull/84) y Vercel confirmó el despliegue.
  Un primer snapshot quedó en conflicto con otra actualización; se regeneró
  desde `main` y se cerró la propuesta obsoleta sin forzar su integración.
- El ensayo editorial `MDH-P04-20261016` se envió al correo autorizado con
  permiso explícito del titular, sin documentos ni una solicitud real. El
  titular confirmó su recepción en el buzón el 16 de octubre de 2026. Esta
  confirmación acredita recepción; la aceptación de envío por sí sola no basta.
- Las pruebas de interfaz y formularios comprueban destinos distintos, ausencia
  de contenido precargado en el correo y compatibilidad con las reevaluaciones
  automáticas. La implementación y su CI se conservan en la
  [PR #81](https://github.com/RauPro/MDH-SV/pull/81).

**P0.4: canales y responsable establecidos; recepción privada comprobada.**

Para repetir el ensayo editorial, enviar únicamente un mensaje ficticio con un
código de prueba, sin noticias reales, documentos ni datos personales. Confirmar
su recepción con el responsable. Registrar fecha, canal y resultado; no publicar
el cuerpo de mensajes reales, sus cabeceras ni información del remitente.

Las pruebas locales deben conservar el contrato del formulario de noticias:
`candidate_id`, `publication_version`, el encabezado `Identificador de la noticia`
y la etiqueta `news-correction`. La explicación libre nunca se ejecuta ni se
envía al modelo. Verificar también una publicación con `validation_only=true`,
su escaneo anterior al push, CI y merge protegido; un conflicto concurrente
exige regenerar desde la base actual, sin sobrescribir el snapshot.
