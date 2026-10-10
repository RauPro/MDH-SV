import { rest } from '../../server/http.mjs';
import { parseFilters, recordSearchSchema, searchRecords } from '../../server/public-data.mjs';
export default rest(url => searchRecords(parseFilters(url.searchParams, recordSearchSchema)));
