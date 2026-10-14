import type { NewsSelectionGate } from '../../../../shared/news-selection.mjs';
import { archiveInstant } from '../newsArchive';
import { NextNewsUpdate } from './NextNewsUpdate';

const number = (value: number) => new Intl.NumberFormat('es-SV').format(value);

export function NewsClassificationProgress({ gate }: { gate: NewsSelectionGate }) {
  if (gate.version === 1) return <p className="filter-note">La clasificación de pertinencia del archivo sigue en curso. Las {number(gate.selected)} noticias disponibles son una selección parcial; no representan el total de noticias pertinentes en El Salvador. Todavía no hay un recuento completo de las referencias evaluadas por pertinencia. La selección no indica que sus fichas estén extraídas o publicadas.</p>;
  const percent = gate.candidates ? 100 * gate.evaluated / gate.candidates : 0;
  return <section className="news-classification-progress" aria-label="Avance del análisis de noticias">
    <div className="classification-heading"><h3>Clasificación de pertinencia</h3><span>{percent.toLocaleString('es-SV', { maximumFractionDigits: 1 })}% evaluado</span></div>
    <p><strong>{number(gate.evaluated)} de {number(gate.candidates)} referencias evaluadas por pertinencia.</strong> Quedan {number(gate.pending)} por clasificar. Una noticia pendiente no es una noticia descartada.</p>
    <progress aria-label="Referencias evaluadas por pertinencia" value={gate.evaluated} max={Math.max(1, gate.candidates)}/>
    <dl className="classification-counts">
      <div><dt>Seleccionadas por pertinencia</dt><dd>{number(gate.selected)}</dd></div>
      <div><dt>Pendientes de clasificar</dt><dd>{number(gate.pending)}</dd></div>
      <div><dt>Fuera de alcance</dt><dd>{number(gate.excluded)}</dd></div>
      <div><dt>Necesitan resolver dudas</dt><dd>{number(gate.uncertain)}</dd></div>
    </dl>
    <p className="classification-note">La clasificación de pertinencia, la extracción de campos y la validación para publicar son etapas distintas. Estos números describen sólo la pertinencia; no indican cuántas fichas están extraídas o publicadas.</p>
    <p className="classification-note">Se incluyen reportes y contexto de derechos humanos vinculados a El Salvador. Este avance describe nuestro archivo, no la totalidad de noticias del país. Actualizado: {archiveInstant(gate.evaluated_at)} (El Salvador).</p>
    <NextNewsUpdate/>
  </section>;
}
