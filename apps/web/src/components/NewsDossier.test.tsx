import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { pendingNewsDossier } from '../../../../shared/news-dossier.mjs';
import { validateNewsSelection } from '../../../../shared/news-selection.mjs';
import fixture from '../__fixtures__/newsArchive.json';
import { NewsDossier } from './NewsDossier';
import type { NewsProcessingEntryStatus } from '../../../../shared/news-processing.mjs';
import { automaticNewsDossier } from '../__fixtures__/automaticNewsDossier';

const reference = fixture.records[0];
const selection = validateNewsSelection(reference.selection);

describe('public pertinence and editorial dossier state remain distinct', () => {
  it('shows the known selection without implying that a private dossier has been extracted', () => {
    const dossier = pendingNewsDossier(reference);
    render(<NewsDossier dossier={dossier} selection={selection}/>);
    const scope = screen.getByRole('region', { name: 'Selección por pertinencia' });
    expect(scope).toHaveTextContent('Seleccionada para seguimiento de derechos humanos');
    expect(scope).toHaveTextContent('Sin revisión humana');
    expect(scope).not.toHaveTextContent('Dato aún no publicado');
    expect(screen.getByText('País').closest('.dossier-field')).toHaveTextContent('Dato aún no publicado');
    expect(screen.getByText(/La selección por pertinencia no indica que estos campos/)).toBeInTheDocument();
    expect(screen.queryByText(/Extracción pendiente|Extracción procesada/)).not.toBeInTheDocument();
  });

  it('never exposes unapproved fields, evidence or private processing state through a public selection', () => {
    const dossier = pendingNewsDossier(reference);
    dossier.extraction_status = 'processed';
    dossier.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: 'incident' };
    dossier.fields.facts = { status: 'reported', value: 'PRIVATE_FACTS_SENTINEL', evidence_refs: ['private'], note: 'PRIVATE_NOTE_SENTINEL' };
    dossier.evidence.push({ id: 'private', url: reference.source_url, locator: 'PRIVATE_EVIDENCE_SENTINEL' });
    render(<NewsDossier dossier={dossier} selection={selection}/>);
    expect(document.body.textContent).not.toMatch(/PRIVATE_|Extracción procesada|Hecho individual reportado/);
    expect(screen.getByText('Descripción de los hechos').closest('.dossier-field')).toHaveTextContent('Dato aún no publicado');
    expect(screen.getByRole('region', { name: 'Selección por pertinencia' })).toHaveTextContent('Seleccionada');
  });

  it.each(['loading', 'unavailable'] as const)('does not assert absence of public approval when its state is %s', reviewState => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)} selection={selection} reviewState={reviewState}/>);
    expect(screen.queryByText(/Ficha pendiente de revisión pública/)).not.toBeInTheDocument();
    expect(screen.queryByText(/todavía no tienen una versión pública disponible/)).not.toBeInTheDocument();
    expect(screen.getByText(reviewState === 'loading' ? /Estamos comprobando si hay una ficha/ : /No se pudo comprobar si hay una ficha/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Selección por pertinencia' })).toHaveTextContent('Seleccionada');
  });

  it('does not infer a selection when no public decision was supplied', () => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)}/>);
    const scope = screen.getByRole('region', { name: 'Selección por pertinencia' });
    expect(within(scope).getAllByText('Sin evaluación pública disponible')).toHaveLength(2);
    expect(scope).not.toHaveTextContent('Seleccionada para seguimiento');
  });

  it('preserves reviewed values, missing-data statuses and the human provenance', () => {
    const dossier = pendingNewsDossier(reference);
    Object.assign(dossier, { human_reviewed: true, extraction_status: 'processed', reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fuente y datos contrastados en revisión editorial.' });
    dossier.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: 'incident' };
    dossier.fields.facts = { status: 'reported', value: 'Hechos atribuidos y revisados.', evidence_refs: ['bibliography'], note: null };
    dossier.fields.affected_persons = { status: 'not_reported', value: null, evidence_refs: [], note: null };
    render(<NewsDossier dossier={dossier} selection={selection}/>);
    expect(screen.getByText(/Ficha revisada por reviewer/)).toHaveTextContent('Extracción procesada');
    expect(screen.getByText('Descripción de los hechos').closest('.dossier-field')).toHaveTextContent('Hechos atribuidos y revisados.');
    expect(screen.getByText('Víctima o personas afectadas').closest('.dossier-field')).toHaveTextContent('Dato no mencionado');
    expect(screen.getByRole('region', { name: 'Selección por pertinencia' })).toHaveTextContent('ficha revisada editorialmente');
    expect(screen.queryByText(/La selección por pertinencia no indica que estos campos/)).not.toBeInTheDocument();
  });
});

describe('automatic public dossiers', () => {
  it('shows validated fields, their evidence and honest provenance without implying human approval', () => {
    const dossier = automaticNewsDossier(reference);
    dossier.fields.affected_persons = { status: 'withheld', value: null, evidence_refs: [], note: 'Identificadores omitidos por protección de las personas.' };
    dossier.fields.instrument = { status: 'not_reported', value: null, evidence_refs: [], note: null };
    render(<NewsDossier dossier={dossier} selection={selection} processingState="unavailable"/>);
    expect(screen.getByText('Ficha publicada automáticamente')).toBeInTheDocument();
    expect(screen.getByText('Resumen ficticio atribuido al medio para pruebas.')).toBeInTheDocument();
    expect(screen.getByText(/Fuente única: no se ha acreditado corroboración independiente/)).toBeInTheDocument();
    expect(screen.queryByText(/Ficha revisada por|Pendiente de validación automática|versión pública disponible/)).not.toBeInTheDocument();
    expect(screen.getByText('Hecho individual reportado')).toBeInTheDocument();
    const summary = screen.getByText('Resumen').closest('.dossier-field')! as HTMLElement;
    expect(within(summary).getByRole('link')).toHaveAttribute('href', reference.source_url);
    const affected = screen.getByText('Víctima o personas afectadas').closest('.dossier-field')! as HTMLElement;
    expect(within(affected).getAllByText('Omitido por privacidad')).toHaveLength(1);
    expect(screen.getByText('Arma o instrumento reportado').closest('.dossier-field')).toHaveTextContent('Dato no mencionado');
    const municipality = screen.getByText('Municipio').closest('.dossier-field')! as HTMLElement;
    expect(within(municipality).getAllByText('No se pudo determinar')).toHaveLength(1);
    expect(within(municipality).getByRole('term')).toHaveTextContent(/^Municipio$/);
  });

  it.each(['missing_automation', 'unbacked_field', 'unknown_metadata'] as const)('never exposes a malformed automatic dossier: %s', kind => {
    const dossier = automaticNewsDossier(reference);
    dossier.fields.summary.value = 'PRIVATE_MALFORMED_SENTINEL';
    if (kind === 'missing_automation') delete dossier.automation;
    if (kind === 'unbacked_field') dossier.fields.summary.evidence_refs = [];
    if (kind === 'unknown_metadata') Object.assign(dossier, { raw_source: 'PRIVATE_RAW_SENTINEL' });
    render(<NewsDossier dossier={dossier} processingState="ready" processingStatus="automatically_published"/>);
    expect(document.body.textContent).not.toContain('PRIVATE_');
    expect(screen.queryByText('Ficha publicada automáticamente')).not.toBeInTheDocument();
    expect(screen.getByText('Estado de extracción sin confirmar')).toBeInTheDocument();
  });

  it('separates private corrections from a public link carrying only candidate and version', () => {
    const dossier = automaticNewsDossier(reference);
    dossier.fields.summary.value = 'A value that must not be sent to GitHub in the link';
    render(<NewsDossier dossier={dossier}/>);
    const link = screen.getByRole('link', { name: 'Corrección pública no sensible' });
    const url = new URL(link.getAttribute('href')!);
    expect(url.origin + url.pathname).toBe('https://github.com/RauPro/MDH-SV/issues/new');
    expect(url.searchParams.get('template')).toBe('news-correction.yml');
    expect(url.searchParams.get('candidate_id')).toBe(reference.id);
    expect(url.searchParams.get('publication_version')).toBe(`${dossier.automation!.policy_version}:${dossier.automation!.source_content_sha256}:${dossier.automation!.validated_at}`);
    expect([...url.searchParams.keys()]).toEqual(['template', 'title', 'candidate_id', 'publication_version']);
    expect(url.searchParams.has('body')).toBe(false);
    expect(url.href).not.toContain(encodeURIComponent(reference.title));
    expect(url.href).not.toContain('A+value');
    expect(screen.getByText(/La solicitud en GitHub es pública/)).toBeInTheDocument();
    const privateLink = screen.getByRole('link', { name: 'Corrección sensible o retirada en privado' });
    expect(privateLink).toHaveAttribute('href', 'mailto:raul.guillen.eng@proton.me');
    expect(new URL(privateLink.getAttribute('href')!).search).toBe('');
    expect(screen.getByText(/El correo abre un mensaje vacío/)).toHaveTextContent('no adjuntes testimonios ni documentos personales');
  });
});


describe('public extraction progress', () => {
  it.each([
    ['pending', 'Pendiente de extracción', 'La ficha todavía no se ha generado.'],
    ['generated', 'Ficha generada · Pendiente de validación automática', 'Ya se generó una ficha, que puede ser parcial.'],
    ['source_unavailable', 'Fuente no accesible para extracción', 'No se pudo acceder a la fuente'],
    ['error', 'Extracción con error', 'no se completó correctamente'],
    ['unknown', 'Estado de extracción sin confirmar', 'No hay un estado de extracción confirmado'],
  ] as const)('distinguishes %s without exposing a private field', (status, label, explanation) => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)} selection={selection} processingState="ready" processingStatus={status}/>);
    const sheet = screen.getByRole('region', { name: 'Ficha estructurada de la noticia' });
    expect(within(sheet).getAllByText(label)).toHaveLength(1);
    expect(sheet).toHaveTextContent(explanation);
    const summary = screen.getByText('Resumen').closest('.dossier-field')! as HTMLElement;
    expect(within(summary).getAllByText('Dato aún no publicado')).toHaveLength(1);
    expect(within(summary).getByRole('term')).toHaveTextContent(/^Resumen$/);
  });

  it.each(['loading', 'unavailable'] as const)('never calls an unconfirmed %s load pending', processingState => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)} processingState={processingState} processingStatus="pending"/>);
    expect(screen.queryByText('Pendiente de extracción')).not.toBeInTheDocument();
    expect(screen.getByText(processingState === 'loading' ? 'Comprobando extracción' : 'Estado de extracción no disponible')).toBeInTheDocument();
  });

  it('does not infer pending when the feed has no entry', () => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)} processingState="ready"/>);
    expect(screen.getByText('Estado de extracción sin confirmar')).toBeInTheDocument();
    expect(screen.queryByText('Pendiente de extracción')).not.toBeInTheDocument();
  });

  it('keeps private values protected even when the public metadata confirms generation', () => {
    const dossier = pendingNewsDossier(reference);
    dossier.extraction_status = 'processed';
    dossier.fields.summary = { status: 'reported', value: 'PRIVATE_SUMMARY_SENTINEL', evidence_refs: [], note: null };
    render(<NewsDossier dossier={dossier} processingState="ready" processingStatus="generated"/>);
    expect(screen.getByText('Ficha generada · Pendiente de validación automática')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('PRIVATE_SUMMARY_SENTINEL');
  });

  it.each(['pending', 'generated', 'error', 'unknown'] as NewsProcessingEntryStatus[])('gives a reviewed dossier priority over %s metadata', processingStatus => {
    const dossier = pendingNewsDossier(reference);
    Object.assign(dossier, { human_reviewed: true, extraction_status: 'processed', reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fuente y datos contrastados en revisión editorial.' });
    dossier.fields.summary = { status: 'reported', value: 'Resumen revisado y aprobado.', evidence_refs: ['bibliography'], note: null };
    render(<NewsDossier dossier={dossier} processingState="ready" processingStatus={processingStatus}/>);
    expect(screen.getByText('Resumen revisado y aprobado.')).toBeInTheDocument();
    expect(screen.getByText(/Ficha revisada por reviewer/)).toBeInTheDocument();
    expect(screen.queryByText('Ficha generada · Pendiente de validación automática')).not.toBeInTheDocument();
    expect(screen.queryByText('Pendiente de extracción')).not.toBeInTheDocument();
  });
});
