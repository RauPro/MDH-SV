export type FeedConsumer = 'private_queue' | 'legacy_scraper';
export interface RegisteredFeed { readonly name: string; readonly url: string; readonly hosts: readonly string[] }
export interface NewsPublisher {
  readonly id: string; readonly name: string; readonly kind: 'wordpress' | 'html_archive';
  readonly endpoint: string; readonly hosts: readonly string[];
  readonly feeds: readonly { readonly url: string; readonly consumers: readonly FeedConsumer[] }[];
  readonly archive_urls?: readonly string[]; readonly seed_urls?: readonly string[];
  readonly access?: 'public' | 'restricted'; readonly notice?: string;
}
export interface SourceRegistry {
  readonly schema_version: 1; readonly publishers: readonly NewsPublisher[];
  readonly primary_evidence_hosts: readonly string[];
  readonly official_statistics_hosts: readonly string[];
  readonly official_statistics_discovery_extra_hosts: readonly string[];
}
export interface DerivedSourceRegistry {
  readonly publishers: readonly NewsPublisher[]; readonly newsHosts: readonly string[];
  readonly primarySourceHosts: readonly string[]; readonly officialStatisticsHosts: readonly string[];
  readonly officialStatisticsDiscoveryHosts: readonly string[];
  readonly publisherHosts: Readonly<Record<string, readonly string[]>>;
  readonly htmlPublishers: readonly string[];
  readonly feeds: Readonly<Record<FeedConsumer, readonly RegisteredFeed[]>>;
}
export function validateSourceRegistry(value: unknown): SourceRegistry;
export function deriveSourceRegistry(value: unknown): DerivedSourceRegistry;
export const NEWS_PUBLISHERS: readonly NewsPublisher[];
export const NEWS_SOURCES: readonly Omit<NewsPublisher, 'feeds'>[];
export const NEWS_HOSTS: readonly string[];
export const PRIMARY_SOURCE_HOSTS: readonly string[];
export const OFFICIAL_STATISTICS_HOSTS: readonly string[];
export const OFFICIAL_STATISTICS_DISCOVERY_HOSTS: readonly string[];
export const PUBLISHER_HOSTS: Readonly<Record<string, readonly string[]>>;
export const HTML_PUBLISHERS: readonly string[];
export const SOURCE_FEEDS: Readonly<Record<FeedConsumer, readonly RegisteredFeed[]>>;
