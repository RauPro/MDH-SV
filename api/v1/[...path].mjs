// Keep a version-level fallback: Vercel's generic API fallback is not selected
// for unknown routes inside this directory when other versioned functions exist.
export { default } from '../[...path].mjs';
