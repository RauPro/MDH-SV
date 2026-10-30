"""Local, opt-in candidate collection. Cannot publish or update the public web dataset."""
import logging
import os
from mdh_pipeline.storage import IncidentRepository
from mdh_pipeline.ai_extractor import HumanRightsExtractor
from mdh_pipeline.scrapers.feed_scrapers import get_all_scrapers
from mdh_schema import SourceEvidence

logger = logging.getLogger(__name__)


def run_periodic_scan(limit_per_outlet=3, enable_wayback=False):
    if not 1 <= limit_per_outlet <= 20:
        raise ValueError('limit_per_outlet must be between 1 and 20')
    if enable_wayback:
        raise ValueError('Archiving requires separate source and privacy review')
    if os.environ.get('MDH_ENABLE_INGESTION') != '1':
        raise RuntimeError('Set MDH_ENABLE_INGESTION=1 to collect private candidates locally')
    repo = IncidentRepository(os.environ.get('MDH_CANDIDATE_DB', '.local-quarantine/candidates.sqlite'))
    # Heuristics by default. External AI requires a separate opt-in for public articles.
    extractor = HumanRightsExtractor(api_key=os.environ.get('GEMINI_API_KEY') if os.environ.get('MDH_ENABLE_AI') == '1' else '')
    count, failures, successes = 0, 0, 0
    for scraper in get_all_scrapers():
        try:
            articles = scraper.fetch_recent_articles(limit=limit_per_outlet)
            successes += 1
            for article in articles:
                if repo.has_source_url(article.url):
                    continue
                evidence = SourceEvidence(outlet_name=article.outlet, original_url=article.url,
                    title=article.title, published_date=article.published_date,
                    source_sha256=article.content_sha256)
                incident = extractor.extract_from_evidence(evidence, article.full_text)
                if incident:
                    repo.save_incident(incident)
                    count += 1
        except Exception:
            failures += 1
            logger.exception('Source collection failed: %s', scraper.outlet_name)
    if not successes:
        raise RuntimeError('All sources failed; no public update was made')
    logger.info('Private candidates: %s; source failures: %s. Public dataset unchanged.', count, failures)
    return count


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_periodic_scan()
