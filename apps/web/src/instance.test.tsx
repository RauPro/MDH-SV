import { expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import catalog from './__fixtures__/catalog.json';
import archive from './__fixtures__/newsArchive.json';
import { automaticNewsDossier } from './__fixtures__/automaticNewsDossier';
import { officialStatistics } from './__fixtures__/officialStatistics';

vi.mock('./instance', () => ({ publicInstance: {
  public_url: 'https://monitor.example.org', public_repository: 'ExampleOrg/monitor',
  editorial_email: 'editor@example.org', reporting_responsible: '@ExampleOrg/editors',
} }));
import App from './App';
import { newsCorrectionUrl } from './newsPublication';

it('a configured fork routes source, documentation and both confidential channels to its own instance', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: unknown) => ({ ok: true, json: async () => {
    const path = String(input);
    if (path.endsWith('/archive/news.json')) return archive;
    if (path.endsWith('/archive/dossiers.json')) return { schema_version: 1, dossiers: [] };
    if (path.endsWith('/statistics/homicides.json')) return officialStatistics;
    return catalog;
  } }) as Response));
  render(<App/>);
  expect(screen.getByRole('link', { name: 'Código y cambios' })).toHaveAttribute('href', 'https://github.com/ExampleOrg/monitor');
  await userEvent.click(screen.getByRole('button', { name: 'Privacidad y correcciones' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('link', { name: 'Corrección sensible o retirada en privado' })).toHaveAttribute('href', 'mailto:editor@example.org');
  expect(within(dialog).getByRole('link', { name: 'Reportar vulnerabilidad técnica en privado' })).toHaveAttribute('href', 'https://github.com/ExampleOrg/monitor/security/advisories/new');
  expect(dialog).toHaveTextContent('Responsable de ambos canales privados: @ExampleOrg/editors');
  const correction = new URL(within(dialog).getByRole('link', { name: 'Registrar corrección no sensible' }).getAttribute('href')!);
  expect(correction.pathname).toBe('/ExampleOrg/monitor/issues/new');
  expect(correction.searchParams.get('template')).toBe('record-correction.yml');
  expect(dialog).not.toHaveTextContent('raul.guillen.eng@proton.me');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cerrar' }));
  await userEvent.click(screen.getByRole('button', { name: 'Datos y API' }));
  expect(await screen.findByRole('link', { name: 'Guía y ejemplos' })).toHaveAttribute('href', 'https://github.com/ExampleOrg/monitor/blob/main/docs/API.md');
  vi.unstubAllGlobals();
});

it('fork news corrections retain only stable publication identifiers and never carry dossier prose', () => {
  const dossier = automaticNewsDossier(archive.records[0]);
  const url = new URL(newsCorrectionUrl(dossier));
  expect(url.origin + url.pathname).toBe('https://github.com/ExampleOrg/monitor/issues/new');
  expect(url.searchParams.get('candidate_id')).toBe(dossier.candidate_id);
  expect(url.searchParams.get('publication_version')).toBe(`${dossier.automation!.policy_version}:${dossier.automation!.source_content_sha256}:${dossier.automation!.validated_at}`);
  expect([...url.searchParams.keys()]).toEqual(['template', 'title', 'candidate_id', 'publication_version']);
  expect(url.searchParams.has('body')).toBe(false);
});
