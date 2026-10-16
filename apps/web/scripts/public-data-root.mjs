import { lstatSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const MAX_BYTES = 64 * 1024 * 1024;

/** Check every ancestor, not just the leaf: external checkouts contain data only. */
export function assertPublicPath(path, type = 'file') {
  const absolute = resolve(path instanceof URL ? fileURLToPath(path) : path);
  let current = parse(absolute).root;
  const parts = absolute.slice(current.length).split(sep).filter(Boolean);
  for (let index = 0; index < parts.length; index += 1) {
    current = join(current, parts[index]);
    const info = lstatSync(current);
    const expected = index === parts.length - 1 ? type : 'directory';
    if (info.isSymbolicLink() || (expected === 'directory' ? !info.isDirectory() : !info.isFile())
      || (expected === 'file' && info.size > MAX_BYTES)) {
      throw new Error('Unsafe public data path');
    }
  }
  return absolute;
}

export function publicDataRoot(args = process.argv.slice(2)) {
  if (args.length !== 0 && (args.length !== 2 || args[0] !== '--root' || !args[1])) {
    throw new Error('Usage: [--root PATH]');
  }
  const root = assertPublicPath(args[1] ?? fileURLToPath(new URL('../../../', import.meta.url)), 'directory');
  return {
    path(name, type = 'file') {
      const target = resolve(root, name);
      const inside = relative(root, target);
      if (isAbsolute(name) || inside === '..' || inside.startsWith(`..${sep}`)) {
        throw new Error('Public data path must stay within its root');
      }
      const url = pathToFileURL(assertPublicPath(target, type));
      if (type === 'directory') url.pathname += '/';
      return url;
    },
  };
}

export function readPublicJson(path) {
  return JSON.parse(readFileSync(assertPublicPath(path), 'utf8'));
}

export function writePublicJson(path, value) {
  const destination = assertPublicPath(path);
  const text = `${JSON.stringify(value, null, 2)}\n`;
  if (readFileSync(destination, 'utf8') === text) return;
  const temporary = join(dirname(destination), `.publication-${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, text, { flag: 'wx', mode: 0o644 });
    assertPublicPath(destination);
    renameSync(temporary, destination);
  } finally {
    rmSync(temporary, { force: true });
  }
}
