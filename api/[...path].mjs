import { rest } from '../server/http.mjs';
import { ApiError } from '../server/public-data.mjs';
export default rest(() => { throw new ApiError(404, 'endpoint_not_found', 'Endpoint no encontrado. Consulte /api/v1/openapi.json.'); });
