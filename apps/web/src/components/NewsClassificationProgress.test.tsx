import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NewsClassificationProgress } from './NewsClassificationProgress';

describe('classification coverage is not national prevalence', () => {
  it('shows evaluated and pending independently of the selected count', () => {
    render(<NewsClassificationProgress gate={{ version: 2, scope: 'human_rights_el_salvador', candidates: 6199,
      selected: 48, evaluated: 59, relevant: 48, excluded: 11, uncertain: 0, pending: 6140, evaluated_at: '2026-10-12T17:00:00Z' }}/>);
    const region = screen.getByRole('region', { name: 'Avance del análisis de noticias' });
    expect(region).toHaveTextContent('59 de 6,199 referencias analizadas');
    expect(region).toHaveTextContent('6,140 por analizar');
    expect(region).toHaveTextContent('Una noticia pendiente no es una noticia descartada');
    expect(screen.getByRole('progressbar', { name: 'Referencias analizadas' })).toHaveAttribute('value', '59');
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '6199');
    expect(screen.getByText('Fuera de alcance').nextElementSibling).toHaveTextContent('11');
  });
  it('does not invent evaluation coverage for an older snapshot', () => {
    render(<NewsClassificationProgress gate={{ version: 1, scope: 'human_rights_el_salvador', candidates: 6199, selected: 48 }}/>);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.getByText(/Todavía no hay un recuento completo/)).toHaveTextContent('no representan el total');
    expect(screen.queryByText(/6,151/)).not.toBeInTheDocument();
  });
});
