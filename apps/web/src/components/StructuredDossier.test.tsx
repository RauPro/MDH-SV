import { expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import data from '../__fixtures__/catalog.json';
import { parseCatalog } from '../catalog';
import { StructuredDossier } from './StructuredDossier';

it('shows the original research fields with evidence while protecting minors', () => {
  const record = parseCatalog(data).records.find(r => r.id === 'mdh-2022-001')!;
  render(<StructuredDossier record={record}/>);
  const section = screen.getByRole('region', {name:'Ficha estructurada de investigación'});
  expect(section).toHaveTextContent('Presunto hecho violatorio');
  expect(section).toHaveTextContent('Víctima o personas afectadas');
  expect(section).toHaveTextContent('Actores señalados por la fuente');
  expect(section).toHaveTextContent('Dato protegido');
  expect(section).toHaveTextContent('No consignado');
  expect(within(section).getByRole('link', {name:/Evidencia 1 de Descripción de los hechos/})).toHaveAttribute('href', record.source_url);
});

it('rejects a dossier with undeclared evidence or contradictory geography', () => {
  const bad = structuredClone(data);
  bad.records[0].dossier.evidence[0].url = 'https://www.hrw.org/undeclared-source';
  expect(() => parseCatalog(bad)).toThrow(/evidencia/);
  const location = structuredClone(data);
  location.records[0].department = 'Sonsonate';
  location.records[0].dossier.location.department.value = 'San Salvador';
  expect(() => parseCatalog(location)).toThrow(/evidencia/);
});
