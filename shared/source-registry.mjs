import document from './source-registry.json' with { type: 'json' };

const required = ['id', 'name', 'kind', 'endpoint', 'hosts', 'feeds'];
const optional = ['archive_urls', 'seed_urls', 'access', 'notice'];
const roles = ['primary_evidence_hosts', 'official_statistics_hosts', 'official_statistics_discovery_extra_hosts'];
const consumers = ['private_queue', 'legacy_scraper'];
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const text = (value, maximum) => typeof value === 'string' && [...value].length > 0 && [...value].length <= maximum
  && value.trim() === value && !/[\u0000-\u001f\u007f]/u.test(value);
const host = value => typeof value === 'string' && value.length <= 253 && value.includes('.') && !/\s/u.test(value)
  && !/^[0-9]+$/.test(value.split('.').at(-1))
  && value.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
const fail = () => { throw new Error('Invalid source registry.'); };
const unique = values => new Set(values).size === values.length;
function hosts(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 100 || !value.every(host) || !unique(value)) fail();
}
function sourceUrl(value, allowedHosts) {
  if (!text(value, 4096) || /[^\x00-\x7f]|[\s\\%?#]/u.test(value)) fail();
  const match = /^https:\/\/([^/]+)(\/.*)$/.exec(value);
  if (!match || !allowedHosts.includes(match[1])) fail();
  let parsed;
  try { parsed = new URL(value); } catch { fail(); }
  if (parsed.hostname !== match[1] || parsed.username || parsed.password || parsed.port) fail();
}
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

/** Validate all identities and capability boundaries before exposing any entry. */
export function validateSourceRegistry(value) {
  if (!exact(value, ['schema_version', 'publishers', ...roles]) || value.schema_version !== 1
      || !Array.isArray(value.publishers) || !value.publishers.length || value.publishers.length > 100) fail();
  roles.forEach(role => hosts(value[role]));
  const roleHosts = roles.flatMap(role => value[role]);
  if (!unique(roleHosts)) fail();
  const ids = new Set(); const names = new Set(); const registeredHosts = new Set(roleHosts);
  for (const publisher of value.publishers) {
    if (!object(publisher) || !required.every(key => Object.hasOwn(publisher, key))
        || Object.keys(publisher).some(key => !required.includes(key) && !optional.includes(key))
        || !text(publisher.id, 80) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(publisher.id)
        || ids.has(publisher.id) || !text(publisher.name, 200) || names.has(publisher.name)
        || !['wordpress', 'html_archive'].includes(publisher.kind)) fail();
    hosts(publisher.hosts);
    if (publisher.hosts.some(item => registeredHosts.has(item))) fail();
    ids.add(publisher.id); names.add(publisher.name); publisher.hosts.forEach(item => registeredHosts.add(item));
    sourceUrl(publisher.endpoint, publisher.hosts);
    if (publisher.kind === 'wordpress' && optional.some(key => Object.hasOwn(publisher, key))) fail();
    if (publisher.kind === 'html_archive') {
      if (!Object.hasOwn(publisher, 'archive_urls') || !text(publisher.notice, 2000)
          || !['public', 'restricted'].includes(Object.hasOwn(publisher, 'access') ? publisher.access : 'public')) fail();
      for (const key of ['archive_urls', 'seed_urls']) {
        const urls = Object.hasOwn(publisher, key) ? publisher[key] : [];
        if (!Array.isArray(urls) || urls.length > 100 || !urls.every(url => typeof url === 'string') || !unique(urls)) fail();
        urls.forEach(url => sourceUrl(url, publisher.hosts));
      }
    }
    if (!Array.isArray(publisher.feeds) || publisher.feeds.length > 10) fail();
    if (publisher.access === 'restricted' && (publisher.feeds.length || publisher.archive_urls?.length || publisher.seed_urls?.length)) fail();
    const feedUrls = new Set(); const feedConsumers = new Set();
    for (const feed of publisher.feeds) {
      if (!exact(feed, ['url', 'consumers'])) fail();
      sourceUrl(feed.url, publisher.hosts);
      if (feedUrls.has(feed.url) || !Array.isArray(feed.consumers) || !feed.consumers.length
          || !feed.consumers.every(consumer => consumers.includes(consumer)) || !unique(feed.consumers)
          || feed.consumers.some(consumer => feedConsumers.has(consumer))) fail();
      feedUrls.add(feed.url); feed.consumers.forEach(consumer => feedConsumers.add(consumer));
    }
  }
  return freeze(structuredClone(value));
}

/** Derivations are capability-specific; no discovered/public data can register a host. */
export function deriveSourceRegistry(value) {
  const registry = validateSourceRegistry(value);
  const newsHosts = registry.publishers.flatMap(publisher => publisher.hosts);
  const publisherHosts = Object.assign(Object.create(null), Object.fromEntries(registry.publishers.map(publisher => [publisher.id, publisher.hosts])));
  return freeze({
    publishers: registry.publishers,
    newsHosts,
    primarySourceHosts: [...registry.primary_evidence_hosts, ...newsHosts],
    officialStatisticsHosts: registry.official_statistics_hosts,
    officialStatisticsDiscoveryHosts: [...registry.official_statistics_hosts, ...registry.official_statistics_discovery_extra_hosts],
    publisherHosts,
    htmlPublishers: registry.publishers.filter(publisher => publisher.kind === 'html_archive').map(publisher => publisher.id),
    feeds: Object.fromEntries(consumers.map(consumer => [consumer, registry.publishers.flatMap(publisher => publisher.feeds
      .filter(feed => feed.consumers.includes(consumer)).map(feed => ({ name: publisher.name, url: feed.url, hosts: publisher.hosts })))])),
  });
}

const registry = deriveSourceRegistry(document);
export const NEWS_PUBLISHERS = registry.publishers;
export const NEWS_SOURCES = freeze(registry.publishers.map(({ feeds: _feeds, ...source }) => source));
export const NEWS_HOSTS = registry.newsHosts;
export const PRIMARY_SOURCE_HOSTS = registry.primarySourceHosts;
export const OFFICIAL_STATISTICS_HOSTS = registry.officialStatisticsHosts;
export const OFFICIAL_STATISTICS_DISCOVERY_HOSTS = registry.officialStatisticsDiscoveryHosts;
export const PUBLISHER_HOSTS = registry.publisherHosts;
export const HTML_PUBLISHERS = registry.htmlPublishers;
export const SOURCE_FEEDS = registry.feeds;
