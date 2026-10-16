const fields = ['schema_version', 'public_url', 'public_repository', 'private_repository', 'public_repository_id', 'publisher_actor_id', 'mcp_allowed_hosts', 'editorial_email', 'reporting_responsible'];
const loopback = host => ['localhost', '127.0.0.1', '[::1]'].includes(host);
const login = '[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?';
const repository = new RegExp(`^${login}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`);
const invalid = () => { throw new Error('Invalid instance configuration.'); };

/** Strict authority syntax, shared by the configuration and trusted platform metadata. */
export function validInstanceHost(value) {
  if (typeof value !== 'string' || value.length > 260) return false;
  const match = /^(\[::1\]|[^:]+)(?::([1-9][0-9]{0,4}))?$/.exec(value);
  if (!match || (match[2] && Number(match[2]) > 65535)) return false;
  const host = match[1];
  if (loopback(host)) return true;
  return host.length <= 253 && host.includes('.') && !/^[0-9]+$/.test(host.split('.').at(-1))
    && host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
}

export function validateInstanceConfig(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== fields.length || fields.some(key => !Object.hasOwn(value, key))) invalid();
  if (value.schema_version !== 1 || !repository.test(value.public_repository) || !repository.test(value.private_repository)
      || value.public_repository.toLowerCase() === value.private_repository.toLowerCase()) invalid();
  for (const key of ['public_repository_id', 'publisher_actor_id']) {
    if (!Number.isSafeInteger(value[key]) || value[key] < 1) invalid();
  }
  if (typeof value.public_url !== 'string' || value.public_url.length > 300 || !/^(https?):\/\/([^/?#]+)\/?$/.test(value.public_url)) invalid();
  const authority = /^(https?):\/\/([^/?#]+)\/?$/.exec(value.public_url);
  if (!validInstanceHost(authority[2])) invalid();
  let url;
  try { url = new URL(value.public_url); } catch { invalid(); }
  if (url.protocol === 'http:' && !loopback(url.hostname)) invalid();
  if (!Array.isArray(value.mcp_allowed_hosts) || !value.mcp_allowed_hosts.length || value.mcp_allowed_hosts.length > 32
      || !value.mcp_allowed_hosts.every(validInstanceHost) || new Set(value.mcp_allowed_hosts).size !== value.mcp_allowed_hosts.length
      || !value.mcp_allowed_hosts.includes(url.host)) invalid();
  if (typeof value.editorial_email !== 'string' || value.editorial_email.length > 254
      || !/^[A-Za-z0-9][A-Za-z0-9._+-]*@[^@]+$/.test(value.editorial_email)
      || !validInstanceHost(value.editorial_email.split('@')[1]) || value.editorial_email.split('@')[1].includes(':')
      || loopback(value.editorial_email.split('@')[1])) invalid();
  if (typeof value.reporting_responsible !== 'string'
      || !new RegExp(`^@${login}(?:/[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?)?$`).test(value.reporting_responsible)) invalid();
  return Object.freeze({ ...value, public_url: url.origin, mcp_allowed_hosts: Object.freeze([...value.mcp_allowed_hosts]) });
}

/** Reject duplicate decoded JSON keys instead of silently accepting the final value. */
export function parseInstanceConfig(raw) {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > 32768) invalid();
  let parsed;
  try { parsed = JSON.parse(raw); } catch { invalid(); }
  const containers = [];
  for (let at = 0; at < raw.length; at += 1) {
    const char = raw[at];
    if (char === '{') containers.push(new Set());
    else if (char === '[') containers.push(null);
    else if (char === '}' || char === ']') containers.pop();
    else if (char === '"') {
      const start = at++;
      while (raw[at] !== '"') { if (raw[at] === '\\') at += 1; at += 1; }
      let after = at + 1;
      while (/\s/.test(raw[after] ?? '') && after < raw.length) after += 1;
      if (raw[after] === ':') {
        const key = JSON.parse(raw.slice(start, at + 1));
        const keys = containers.at(-1);
        if (!keys || keys.has(key)) invalid();
        keys.add(key);
      }
    }
  }
  return validateInstanceConfig(parsed);
}

/** Only these nonsecret fields are compiled into the browser bundle. */
export function publicInstanceConfig(instance) {
  return Object.freeze(Object.fromEntries(['public_url', 'public_repository', 'editorial_email', 'reporting_responsible'].map(key => [key, instance[key]])));
}
