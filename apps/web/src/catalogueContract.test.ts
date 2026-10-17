import { describe, expect, it } from 'vitest';
import { loadContractCases } from '../../../tests/contract-fixtures.mjs';
import { parseCatalog } from './catalog';

describe('public catalogue shared wire corpus', () => {
  const relative = '../../../tests/fixtures/contracts/catalogue.json';
  for (const { name, valid, document } of loadContractCases(new URL(relative, import.meta.url))) {
    it(name, () => {
      const before = structuredClone(document);
      if (valid) expect(() => parseCatalog(document)).not.toThrow();
      else expect(() => parseCatalog(document)).toThrow();
      expect(document).toEqual(before);
    });
  }
});
