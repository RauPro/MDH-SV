import { rest } from '../../../server/http.mjs';
import { ApiError, getRecord } from '../../../server/public-data.mjs';
export default rest((url, req) => {
  let id;
  // Vercel appends the dynamic route parameter to both req.query and req.url.
  // The actual path remains authoritative so a query parameter cannot select another record.
  try { id = decodeURIComponent(url.pathname.split('/').at(-1)); }
  catch { throw new ApiError(400, 'invalid_id', 'Identificador inválido.'); }
  const routingIds = url.searchParams.getAll('id');
  if ([...url.searchParams.keys()].some(key => key !== 'id') || routingIds.length > 1
    || (routingIds.length === 1 && routingIds[0] !== id)
    || (req.query?.id !== undefined && req.query.id !== id)) {
    throw new ApiError(400, 'invalid_query', 'El identificador de consulta debe coincidir con la ruta; no se admiten filtros.');
  }
  return getRecord(id);
});
