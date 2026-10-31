// Vercel starts the analysis at minute 07 of hours 01 and 13 UTC. Publication follows processing.
export const NEWS_ANALYSIS_MINUTE = 7;
export const NEWS_ANALYSIS_HOURS = [1, 13];

export function nextNewsAnalysis(now: number): string {
  const next = new Date(now);
  next.setUTCMinutes(NEWS_ANALYSIS_MINUTE, 0, 0);
  while (!NEWS_ANALYSIS_HOURS.includes(next.getUTCHours()) || next.getTime() <= now) {
    next.setUTCHours(next.getUTCHours() + 1);
  }
  return next.toISOString();
}
