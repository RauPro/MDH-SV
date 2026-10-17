import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/** Declarative fixtures only: exact add/replace/remove operations, never executable code. */
export function loadContractCases(path) {
  const fixture = JSON.parse(readFileSync(path, 'utf8'));
  const keys = (value, expected) => assert.deepEqual(Object.keys(value).sort(), expected.toSorted());
  keys(fixture, ['schema_version', 'contract', 'bases', 'cases']);
  assert.equal(fixture.schema_version, 1);
  assert.equal(typeof fixture.contract, 'string');
  assert.ok(fixture.contract && fixture.bases && !Array.isArray(fixture.bases) && Array.isArray(fixture.cases));
  const names = new Set();
  const results = fixture.cases.map(example => {
    keys(example, ['name', 'base', 'valid', 'operations']);
    assert.ok(typeof example.name === 'string' && example.name && !names.has(example.name));
    assert.equal(typeof example.valid, 'boolean');
    assert.ok(Array.isArray(example.operations) && Object.hasOwn(fixture.bases, example.base));
    names.add(example.name);
    let document = structuredClone(fixture.bases[example.base]);
    for (const operation of example.operations) {
      const { op, path: route } = operation;
      assert.ok(['add', 'remove', 'replace'].includes(op));
      keys(operation, op === 'remove' ? ['op', 'path'] : ['op', 'path', 'value']);
      assert.ok(Array.isArray(route) && route.every(key => typeof key === 'string' || Number.isSafeInteger(key)));
      if (!route.length) {
        assert.equal(op, 'replace');
        document = structuredClone(operation.value);
        continue;
      }
      let target = document;
      for (const key of route.slice(0, -1)) {
        assert.ok(target && typeof target === 'object' && Object.hasOwn(target, key));
        if (Array.isArray(target)) assert.ok(Number.isSafeInteger(key) && key >= 0 && key < target.length);
        target = target[key];
      }
      const key = route.at(-1);
      assert.ok(target && typeof target === 'object');
      if (Array.isArray(target)) {
        assert.ok(Number.isSafeInteger(key) && key >= 0 && key <= target.length);
        if (op === 'add') { target.splice(key, 0, structuredClone(operation.value)); continue; }
        assert.ok(key < target.length);
        if (op === 'remove') { target.splice(key, 1); continue; }
      } else {
        assert.equal(typeof key, 'string');
        assert.equal(Object.hasOwn(target, key), op !== 'add');
      }
      if (op === 'remove') delete target[key];
      else Object.defineProperty(target, key, { value: structuredClone(operation.value), enumerable: true, configurable: true, writable: true });
    }
    return { name: example.name, valid: example.valid, document };
  });
  assert.ok(results.length);
  return results;
}
