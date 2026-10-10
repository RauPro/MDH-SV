import { rest } from '../../server/http.mjs';
import { getStats, parseFilters, statsSchema } from '../../server/public-data.mjs';
export default rest(url => getStats(parseFilters(url.searchParams, statsSchema)));
