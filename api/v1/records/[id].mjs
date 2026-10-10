import { rest } from '../../../server/http.mjs';
import { ApiError, getRecord } from '../../../server/public-data.mjs';
export default rest((url, req) => {
  if (url.searchParams.size) throw new ApiError(400, 'invalid_query', 'Este endpoint no admite filtros.');
  let id;
  try { id = req.query?.id ?? decodeURIComponent(url.pathname.split('/').at(-1)); }
  catch { throw new ApiError(400, 'invalid_id', 'Identificador inválido.'); }
  return getRecord(id);
});
