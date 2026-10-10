import { rest } from '../../server/http.mjs';
import { newsSearchSchema, parseFilters, searchNews } from '../../server/public-data.mjs';
export default rest(url => searchNews(parseFilters(url.searchParams, newsSearchSchema)));
