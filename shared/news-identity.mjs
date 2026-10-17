import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';
import { NEWS_SOURCES } from './source-registry.mjs';

// Hash the exact UTF-8 source URL, matching hashlib.sha256 in the pipeline.
// These are public identifiers, not signatures or approval of article contents.
export function sourceIdentityMatches(identifier, value) {
  if (typeof identifier !== 'string' || !/^[a-f0-9]{64}$/.test(identifier)
      || typeof value !== 'string' || value.length > 4096) return false;
  const bytes = new TextEncoder().encode(value);
  if (new TextDecoder().decode(bytes) !== value) return false;
  return identifier === bytesToHex(sha256(bytes));
}

/** A public article URL is already canonical: no tracking query or fragment. */
export function canonicalNewsSource(value) {
  if (typeof value !== 'string' || value.length > 4096 || /[\u0000-\u001f<>\\]/.test(value)) return null;
  const parts = /^https:\/\/([^/?#]+)(\/[^?#]*)$/u.exec(value);
  if (!parts || [...parts[2]].length < 5) return null;
  return NEWS_SOURCES.find(source => source.hosts.includes(parts[1])) ?? null;
}
