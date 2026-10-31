// Latency measurement for tests/api-budgets.test.mjs: prints JSON timings.
const started = performance.now();
const { searchNews } = await import('../server/public-data.mjs');
const cold = performance.now() - started;

const mixed = [
  { limit: 1 }, { limit: 25, coverage: 'summary' }, { q: 'detención', limit: 3 },
  { publisher: 'El Faro', limit: 5 }, { q: 'mujer', offset: 10, limit: 7 },
  { scope_hint: 'el_salvador', limit: 4 }, { date_from: '2020-01-01', date_to: '2024-12-31', limit: 9 },
];
const warm = [];
for (let round = 0; round < 40; round += 1) {
  const filters = mixed[round % mixed.length];
  const mark = performance.now();
  searchNews(filters);
  warm.push(performance.now() - mark);
}
warm.sort((a, b) => a - b);
const p95 = warm[Math.ceil(warm.length * 0.95) - 1];
console.log(JSON.stringify({ cold, warm_p95: p95 }));
