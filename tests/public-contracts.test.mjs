import assert from 'node:assert/strict';
import test from 'node:test';
import { loadContractCases } from './contract-fixtures.mjs';
import { validateNewsSelection } from '../shared/news-selection.mjs';
import { validatePublishedNewsDossier } from '../shared/news-dossier.mjs';
import { parseNewsProcessing } from '../shared/news-processing.mjs';
import { parsePublishedNewsLocations } from '../shared/news-location.mjs';

const validators = {
  'news-selection': validateNewsSelection,
  'news-dossier': validatePublishedNewsDossier,
  'news-processing': parseNewsProcessing,
  'news-location': parsePublishedNewsLocations,
};
for (const [contract, validate] of Object.entries(validators)) {
  for (const { name, valid, document } of loadContractCases(new URL(`./fixtures/contracts/${contract}.json`, import.meta.url))) {
    test(`${contract}: ${name}`, () => {
      if (valid) assert.doesNotThrow(() => validate(document));
      else assert.throws(() => validate(document));
    });
  }
}
