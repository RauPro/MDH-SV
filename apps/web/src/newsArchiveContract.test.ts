import { expect, it } from 'vitest';
import { loadContractCases } from '../../../tests/contract-fixtures.mjs';
import { parseNewsArchive, validateNewsReference } from '../../../shared/news-archive.mjs';

for (const [kind, validate] of [['news-reference', validateNewsReference], ['news-archive', parseNewsArchive]] as const) {
  const relative = `../../../tests/fixtures/contracts/${kind}.json`;
  for (const { name, valid, document } of loadContractCases(new URL(relative, import.meta.url))) {
    it(`${kind}: ${name}`, () => {
      if (valid) expect(() => validate(document)).not.toThrow();
      else expect(() => validate(document)).toThrow();
    });
  }
}
