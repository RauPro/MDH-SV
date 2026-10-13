import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { pendingNewsDossier } from '../../../../shared/news-dossier.mjs';
import { validateNewsSelection } from '../../../../shared/news-selection.mjs';
import fixture from '../__fixtures__/newsArchive.json';
import { NewsDossier } from './NewsDossier';

const reference = fixture.records[0];
const selection = validateNewsSelection(reference.selection);

describe('public pertinence and editorial dossier state remain distinct', () => {
  it('shows the known selection without implying that a private dossier has been extracted', () => {
    const dossier = pendingNewsDossier(reference);
    render(<NewsDossier dossier={dossier} selection={selection}/>);
    const scope = screen.getByRole('region', { name: 'Selección por pertinencia' });
    expect(scope).toHaveTextContent('Seleccionada para seguimiento de derechos humanos');
    expect(scope).toHaveTextContent('Sin revisión humana');
    expect(scope).not.toHaveTextContent('Sin dato revisado disponible');
    expect(screen.getByText('País').closest('.dossier-field')).toHaveTextContent('Sin dato revisado disponible');
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
    expect(screen.getByText('Descripción de los hechos').closest('.dossier-field')).toHaveTextContent('Sin dato revisado disponible');
    expect(screen.getByRole('region', { name: 'Selección por pertinencia' })).toHaveTextContent('Seleccionada');
  });

  it.each(['loading', 'unavailable'] as const)('does not assert absence of public approval when its state is %s', reviewState => {
    render(<NewsDossier dossier={pendingNewsDossier(reference)} selection={selection} reviewState={reviewState}/>);
    expect(screen.queryByText(/Ficha pendiente de revisión pública/)).not.toBeInTheDocument();
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
    expect(screen.getByText('Víctima o personas afectadas').closest('.dossier-field')).toHaveTextContent('Dato no mencionado en el texto analizado');
    expect(screen.getByRole('region', { name: 'Selección por pertinencia' })).toHaveTextContent('ficha revisada editorialmente');
    expect(screen.queryByText(/La selección por pertinencia no indica que estos campos/)).not.toBeInTheDocument();
  });
});
