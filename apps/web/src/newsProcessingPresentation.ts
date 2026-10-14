import type { NewsProcessingStatus } from '../../../shared/news-processing.mjs';

export type PublicLoadState = 'loading' | 'ready' | 'unavailable';

type ProcessingPresentation = { label: string; explanation: string };

const extractionPresentation: Record<Exclude<NewsProcessingStatus, 'reviewed'>, ProcessingPresentation> = {
  pending: {
    label: 'Pendiente de extracción',
    explanation: 'La ficha todavía no se ha generado. Después de la extracción, sus campos deberán pasar por revisión editorial antes de publicarse.',
  },
  generated: {
    label: 'Ficha generada · Pendiente de revisión',
    explanation: 'Ya se generó una ficha, que puede ser parcial. Sus campos permanecen privados hasta que se complete la revisión editorial.',
  },
  source_unavailable: {
    label: 'Fuente no accesible para extracción',
    explanation: 'No se pudo acceder a la fuente durante el intento de extracción. Esto no significa que la noticia carezca de información.',
  },
  error: {
    label: 'Extracción con error',
    explanation: 'El intento de generar la ficha no se completó correctamente. No hay una ficha generada confirmada para publicar.',
  },
  unknown: {
    label: 'Estado de extracción sin confirmar',
    explanation: 'No hay un estado de extracción confirmado para esta noticia. No podemos afirmar que su ficha esté pendiente o que ya haya sido generada.',
  },
};

export function newsProcessingPresentation({ humanReviewed, reviewState, processingState, status }: {
  humanReviewed: boolean;
  reviewState: PublicLoadState;
  processingState: PublicLoadState;
  status?: NewsProcessingStatus;
}): ProcessingPresentation {
  if (humanReviewed) return { label: 'Ficha revisada editorialmente', explanation: 'Los campos publicados corresponden a la ficha aprobada en revisión editorial.' };
  if (reviewState === 'loading') return { label: 'Comprobando revisión pública', explanation: 'Estamos comprobando si hay una ficha pública revisada disponible.' };
  if (reviewState === 'unavailable') return { label: 'Revisión pública no disponible', explanation: 'No se pudo comprobar si hay una ficha pública revisada disponible.' };
  if (processingState === 'loading') return { label: 'Comprobando extracción', explanation: 'Estamos consultando si la ficha está pendiente de extracción o ya fue generada.' };
  if (processingState === 'unavailable') return { label: 'Estado de extracción no disponible', explanation: 'No se pudo comprobar el estado de extracción. No podemos afirmar que la ficha esté pendiente o que ya haya sido generada.' };
  return extractionPresentation[status === 'reviewed' ? 'unknown' : status ?? 'unknown'];
}
