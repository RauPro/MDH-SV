import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import config from '../../../../vercel.json';
import { NEWS_ANALYSIS_MINUTE, nextNewsAnalysis } from '../newsSchedule';
import { NextNewsUpdate } from './NextNewsUpdate';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('next scheduled news analysis', () => {
  it('matches the deployed cron without mistaking the fallback for another update', () => {
    expect(config.crons.find(cron => cron.path === '/api/internal/hourly-ingestion')?.schedule)
      .toBe(`${NEWS_ANALYSIS_MINUTE} * * * *`);
  });

  it.each([
    ['2026-10-12T20:06:59.999Z', '2026-10-12T20:07:00.000Z'],
    ['2026-10-12T20:07:00.000Z', '2026-10-12T21:07:00.000Z'],
    ['2026-12-31T23:59:59.999Z', '2027-01-01T00:07:00.000Z'],
  ])('keeps a strictly future schedule from %s', (now, expected) => {
    expect(nextNewsAnalysis(Date.parse(now))).toBe(expected);
  });

  it('shows Salvadoran time and advances while the page stays open', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-12T20:06:59Z'));
    const { container, unmount } = render(<NextNewsUpdate/>);
    expect(container.querySelector('time')).toHaveAttribute('datetime', '2026-10-12T20:07:00.000Z');
    expect(container.querySelector('time')).toHaveTextContent('2:07');
    expect(screen.getByText(/Los resultados se publican al terminar/)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1001));
    expect(container.querySelector('time')).toHaveAttribute('datetime', '2026-10-12T21:07:00.000Z');
    expect(container.querySelector('time')).toHaveTextContent('3:07');
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('catches up after a suspended tab without replaying stale timer ticks', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-12-31T23:00:00Z'));
    const { container } = render(<NextNewsUpdate/>);
    vi.setSystemTime(new Date('2027-01-01T07:30:00Z'));
    act(() => window.dispatchEvent(new Event('focus')));
    expect(container.querySelector('time')).toHaveAttribute('datetime', '2027-01-01T08:07:00.000Z');
    expect(container.querySelector('time')).toHaveTextContent('2:07');
    expect(vi.getTimerCount()).toBe(1);
  });
});
