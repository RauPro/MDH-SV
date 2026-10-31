import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Page } from '@playwright/test';

// The specs derive expectations from the committed public data, because the
// archive counts and identifiers change with every publication cycle.
export function readDistJson<T>(relative: string): T {
  return JSON.parse(
    readFileSync(fileURLToPath(new URL(`../dist/${relative}`, import.meta.url)), 'utf8'),
  ) as T;
}

export interface NewsReferenceLite {
  id: string;
  title: string;
  publisher: string;
  topics: string[];
  selection: unknown;
  published_date: string;
}
export interface NewsArchiveLite {
  records: NewsReferenceLite[];
  policy: { selection_gate: { version: number } };
  coverage: { sources: { id: string; publisher: string }[] };
}
export interface DossiersLite {
  dossiers: { candidate_id: string }[];
}
export interface CatalogLite {
  records: { id: string; title: string; record_type: string; department?: string }[];
}

export const newsArchive = () => readDistJson<NewsArchiveLite>('archive/news.json');
export const newsDossiers = () => readDistJson<DossiersLite>('archive/dossiers.json');
export const catalog = () => readDistJson<CatalogLite>('data/catalog.json');

// Same normalisation the web filter applies before matching (NFD + strip
// combining marks + Spanish lowercase), so expected counts match the UI.
export const normalize = (value: string) =>
  value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLocaleLowerCase('es').trim();

// src/newsArchive.ts topicLabels; kept small and stable.
const topicLabels: Record<string, string> = {
  detencion_y_debido_proceso: 'Detención y debido proceso',
  condiciones_de_reclusion: 'Condiciones de reclusión',
  libertad_de_expresion: 'Libertad de expresión',
  defensores_y_sociedad_civil: 'Defensores y sociedad civil',
  violencia_y_desapariciones: 'Violencia y desapariciones',
  violencia_sexual_y_discriminacion: 'Violencia sexual y discriminación',
  derechos_sociales_y_ambientales: 'Derechos sociales y ambientales',
  derechos_humanos: 'Derechos humanos',
};
const topicLabel = (topic: string) => topicLabels[topic] ?? topic.replaceAll('_', ' ');

const corpus = (record: NewsReferenceLite) =>
  normalize([record.title, record.publisher, ...record.topics.map(topicLabel)].join(' '));

// The subset filterNews() can return for a single-term query.
export function queryMatches(term: string): NewsReferenceLite[] {
  const needle = normalize(term);
  return newsArchive().records.filter(
    record => record.selection && corpus(record).includes(needle),
  );
}

export const selectedRecords = () =>
  newsArchive().records.filter(record => record.selection);

export function dossierBackedReference() {
  const covered = new Set(newsDossiers().dossiers.map(dossier => dossier.candidate_id));
  const reference = newsArchive().records.find(record => covered.has(record.id));
  if (!reference) throw new Error('no dossier-backed news reference in archive/news.json');
  return reference;
}

// Console errors and uncaught page errors, collected for the whole spec run.
export function consoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', error => errors.push(error.message));
  return errors;
}

// CSP violations need an in-page listener registered before the app boots.
// Returns a reader for the violations accumulated so far.
export async function cspViolations(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    const collected: string[] = [];
    document.addEventListener('securitypolicyviolation', event => {
      collected.push(`${event.violatedDirective} ${event.blockedURI}`);
    });
    (window as unknown as { __cspViolations: string[] }).__cspViolations = collected;
  });
  return () =>
    page.evaluate(
      () => (window as unknown as { __cspViolations: string[] }).__cspViolations,
    );
}

// Every request outside the replica is aborted and recorded; specs assert the
// list stays empty (fonts and icons are bundled, nothing else should leave).
export function externalRequests(page: Page): string[] {
  const external: string[] = [];
  void page.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith('http://127.0.0.1:')) {
      void route.continue();
    } else {
      external.push(url);
      void route.abort();
    }
  });
  return external;
}

interface AxeViolation {
  id: string;
  impact: string | null;
  nodes: { target: string[] }[];
}
interface AxeResults {
  violations: AxeViolation[];
}

const axeSource = readFileSync(
  fileURLToPath(new URL('../node_modules/axe-core/axe.min.js', import.meta.url)),
  'utf8',
);

// page.evaluate is CSP-safe: the script text executes through the CDP channel
// instead of a page <script> tag, which CSP would block under script-src 'self'.
export async function runAxe(page: Page): Promise<AxeViolation[]> {
  await page.evaluate(axeSource);
  const results = await page.evaluate(
    () =>
      (window as unknown as { axe: { run(doc: Document, options: unknown): Promise<AxeResults> } })
        .axe.run(document, {
          runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
        }),
  );
  return results.violations.filter(
    violation => violation.impact === 'serious' || violation.impact === 'critical',
  );
}

export function formatViolations(violations: AxeViolation[]): string {
  return violations
    .map(violation => `${violation.id} (${violation.impact}): ${violation.nodes.map(node => node.target.join(' ')).join(', ')}`)
    .join('\n');
}
