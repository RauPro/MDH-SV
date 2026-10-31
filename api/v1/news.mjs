import { rest } from '../../server/http.mjs';
import { newsCoverageDeprecation, newsSearchSchema, parseFilters, searchNews } from '../../server/public-data.mjs';

// The injectable clock keeps the deprecation/sunset boundary testable.
export const newsHandler = (now = () => new Date()) => rest((url, req, res) => {
  const filters = parseFilters(url.searchParams, newsSearchSchema);
  const instant = now();
  const result = searchNews(filters, undefined, undefined, undefined, undefined, instant);
  if (!('coverage' in filters)) {
    for (const [key, value] of Object.entries(newsCoverageDeprecation(instant))) res.setHeader(key, value);
  }
  return result;
});

export default newsHandler();
