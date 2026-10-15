"""Single inclusive editorial policy shared by scope, extraction and exclusion checks."""

SCOPE_PROMPT = "mdh-news-scope-4-inclusive-violence"
EXCLUSION_PROMPT = "mdh-news-exclusion-1-inclusive-violence"
POLICY_VERSION = "inclusive-violence-1"
CURRENT_SCOPE_PROMPTS = {SCOPE_PROMPT, EXCLUSION_PROMPT}

SCOPE_POLICY = """Evalúa dos ejes sobre el mismo asunto sustantivo de la noticia:
1. jurisdiction=SV requiere una relación sustantiva con El Salvador o con personas
salvadoreñas, incluso en el extranjero, que conste en el texto y corresponda al asunto
relevante. Usa other para un asunto de otro país sin ese vínculo y unclear cuando falte
información. El nombre del medio, su marca, la firma, su sede, un apellido o una mención
incidental de Bukele no prueban esa relación. No inventes nacionalidades ni ubicaciones.
2. relevance=relevant incluye posibles violaciones y contexto de derechos humanos:
leyes, políticas, personas defensoras, memoria histórica, justicia transicional,
reparación, discriminación, libertad de expresión, condiciones de detención, salud,
alimentación, rehabilitación, reinserción, hacinamiento y debido proceso; también
violencia de género o contra la niñez, feminicidio, desaparición, muerte bajo custodia,
migración, trabajo, vivienda, agua, educación y acceso a la salud.
Incluye TODOS los reportes de homicidios vinculados sustantivamente a El Salvador o a
personas salvadoreñas desde 2010 hasta hoy: hechos individuales o colectivos, cifras,
tendencias y seguimiento policial o judicial (capturas, investigaciones, acusaciones,
juicios y condenas). El homicidio y su contexto histórico son pertinentes por sí mismos:
NO exijas una denuncia adicional de abuso estatal, discriminación, un patrón, la frase
«derechos humanos» ni una calificación jurídica. Incluye violencia de actores no estatales.
Una violación o denuncia no requiere sentencia judicial. Clasificar como relevant NO
certifica los hechos ni atribuye responsabilidad: conserva las atribuciones y la privacidad.
Las noticias internacionales de derechos pueden tener relevance=relevant y jurisdiction=other;
no combines un tema internacional con una mención salvadoreña incidental sobre otro asunto.
No clasifiques automáticamente como not_relevant porque el artículo sea policial, judicial,
de seguridad o no use vocabulario jurídico. Un hecho violento reportado con pocos detalles
sigue siendo pertinente. Las incertidumbres de una ficha no justifican descartar la noticia.
Reserva not_relevant para asuntos claramente ajenos al alcance, como deportes,
entretenimiento o comercio sin vínculo sustantivo con las categorías anteriores.
Cuando haya duda, texto incompleto, extracción dudosa o información insuficiente usa unclear;
la duda mantiene pendiente el artículo y nunca equivale a exclusión definitiva.
Una incompatibilidad manifiesta entre la identidad del artículo y su cuerpo también exige
unclear: no clasifiques otro texto como si fuera la noticia buscada. Una nota con varios
hechos no constituye por sí sola esa incompatibilidad.
En noticias con varios hechos, basta que AL MENOS UNO cumpla estos criterios; no descartes
una muerte bajo custodia o un hecho relevante porque otro ocupe el titular. La cobertura
regional que trate sustantivamente a El Salvador también cuenta, aunque no sea el único
país ni el asunto mayoritario. Una mención incidental sin contexto pertinente no basta.
Considera todos los párrafos, incluidos los que contradigan una primera impresión.
"""
