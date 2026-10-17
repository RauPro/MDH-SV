import assert from 'node:assert/strict';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

/** Execute the exact OpenAPI 3.1 response fragments, with local references only. */
export function responseContracts(document) {
  assert.equal(document.openapi, '3.1.0');
  const ajv = new Ajv2020({
    strict: true, strictTypes: false, strictRequired: false, allowUnionTypes: true,
    allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false,
  });
  addFormats(ajv, { mode: 'full' });
  // Preserve #/components/schemas references exactly. This wrapper annotation
  // does not add validation rules or transform any response schema.
  ajv.addKeyword({ keyword: 'components', schemaType: 'object' });
  function localReferences(value) {
    if (!value || typeof value !== 'object') return;
    if (Object.hasOwn(value, '$ref')) assert.match(value.$ref, /^#\/components\/schemas\/[^/]+$/);
    for (const child of Object.values(value)) localReferences(child);
  }
  localReferences(document);
  const compile = schema => ajv.compile({ ...schema, components: document.components });
  const components = Object.fromEntries(Object.keys(document.components.schemas)
    .map(name => [name, compile({ $ref: `#/components/schemas/${name}` })]));
  const responses = new Map();
  for (const [path, operations] of Object.entries(document.paths)) {
    for (const [method, operation] of Object.entries(operations)) {
      for (const [status, response] of Object.entries(operation.responses)) {
        const schema = response.content?.['application/json']?.schema;
        responses.set(`${method.toUpperCase()} ${path} ${status}`, schema ? compile(schema) : null);
      }
    }
  }
  const describe = errors => JSON.stringify(errors?.map(({ instancePath, schemaPath, keyword, message }) => ({ instancePath, schemaPath, keyword, message })));
  function validate(path, status, value, method = 'GET') {
    const key = `${method} ${path} ${status}`;
    assert.ok(responses.has(key), `Missing declared response: ${key}`);
    const validator = responses.get(key);
    if (validator === null) assert.equal(value, '', `${key} must have no body`);
    else assert.ok(validator(value), `${key}: ${describe(validator.errors)}`);
  }
  return { validate, components, compile, responses };
}
