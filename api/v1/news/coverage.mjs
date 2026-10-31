import { rest } from '../../../server/http.mjs';
import { ApiError, newsCoverage } from '../../../server/public-data.mjs';
export default rest(url => {
  if (url.searchParams.size) throw new ApiError(400, 'invalid_query', 'El contrato no admite filtros.');
  return newsCoverage();
});
