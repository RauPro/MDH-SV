import { ApiError, PUBLIC_URL } from './public-data.mjs';

export function cors(res, methods = 'GET, HEAD, OPTIONS') {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', methods);
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept, MCP-Protocol-Version, MCP-Session-Id');
  res.setHeader('Access-Control-Expose-Headers', 'MCP-Protocol-Version, MCP-Session-Id, Deprecation, Sunset, Link');
  res.setHeader('Access-Control-Max-Age', '86400');
  res.setHeader('X-Content-Type-Options', 'nosniff');
}

export function json(res, status, body, head = false) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', status === 200 ? 'public, max-age=60, s-maxage=300' : 'no-store');
  res.end(head ? undefined : JSON.stringify(body));
}

export function rest(callback) {
  return async (req, res) => {
    cors(res);
    if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.setHeader('Allow', 'GET, HEAD, OPTIONS');
      json(res, 405, { error: { code: 'method_not_allowed', message: 'La API pública solo permite lectura.' } });
      return;
    }
    try {
      if ((req.url ?? '').length > 4096) throw new ApiError(414, 'uri_too_long', 'La consulta excede el tamaño permitido.');
      const url = new URL(req.url, PUBLIC_URL);
      json(res, 200, await callback(url, req, res), req.method === 'HEAD');
    } catch (error) {
      if (!(error instanceof ApiError)) console.error('Public API failure:', error.name);
      json(res, error.status ?? 500, { error: { code: error.code ?? 'internal_error', message: error instanceof ApiError ? error.message : 'No se pudo consultar el catálogo público.' } }, req.method === 'HEAD');
    }
  };
}
