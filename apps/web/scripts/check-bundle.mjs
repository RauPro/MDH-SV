#!/usr/bin/env node
// Hard budget on the built JS gzip size (measured baseline × 1.25).
// The default-view payload is documented in docs/QUALITY.md.
import { gzipSync } from 'node:zlib';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const BUDGET = 149_444; // 119 555 bytes gzip measured 2026-10-31 × 1.25
const assets = fileURLToPath(new URL('../dist/assets', import.meta.url));
const bundles = readdirSync(assets).filter(name => /^index-.*\.js$/.test(name));
if (bundles.length !== 1) throw new Error(`expected exactly one index JS bundle, found ${bundles.length}`);
const size = gzipSync(readFileSync(join(assets, bundles[0]))).length;
console.log(`index JS gzip: ${size} bytes (budget ${BUDGET})`);
if (size > BUDGET) throw new Error(`index JS gzip ${size} exceeds the ${BUDGET}-byte budget`);
