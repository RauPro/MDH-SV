// Vercel starts the hourly analysis at minute 07. Publication follows processing.
export const NEWS_ANALYSIS_MINUTE = 7;

export function nextNewsAnalysis(now: number): string {
  const next = new Date(now);
  next.setUTCMinutes(NEWS_ANALYSIS_MINUTE, 0, 0);
  if (next.getTime() <= now) next.setUTCHours(next.getUTCHours() + 1);
  return next.toISOString();
}
