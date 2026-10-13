import { rest } from '../../server/http.mjs';
import { getOfficialStatistics, parseOfficialStatisticsFilters } from '../../server/official-statistics.mjs';

export default rest(url => getOfficialStatistics(parseOfficialStatisticsFilters(url.searchParams)));
