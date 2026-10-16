import { lstatSync, readFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { parseInstanceConfig } from '../shared/instance-config.mjs';

export function loadInstanceConfig(environment = process.env) {
  try {
    const override = environment.MDH_INSTANCE_FILE;
    if (override !== undefined && (typeof override !== 'string' || !isAbsolute(override))) throw new Error();
    const path = override ?? new URL('../instance.json', import.meta.url);
    const info = lstatSync(path);
    if (!info.isFile() || info.size < 1 || info.size > 32768) throw new Error();
    return parseInstanceConfig(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(path)));
  } catch { throw new Error('Invalid instance configuration.'); }
}

export const instance = loadInstanceConfig();
export let PUBLIC_URL = instance.public_url;

// Called only by the local executable before it imports the API handlers.
// The request Host/Forwarded headers never participate in this trusted override.
export function configureLocalListener(port) {
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be an integer between 1 and 65535.');
  PUBLIC_URL = `http://127.0.0.1:${port}`;
}
