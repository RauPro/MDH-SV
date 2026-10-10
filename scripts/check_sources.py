"""Read-only link check. A successful HTTP response is not factual validation."""
import concurrent.futures
import requests
from mdh_pipeline.publication import load_catalog


def check(url):
    try:
        response = requests.get(url, timeout=(10, 30), stream=True,
            headers={'User-Agent': 'MDH-SV source-link-check/2.0 (+https://github.com/RauPro/MDH-SV)'})
        with response:
            return {'url': url, 'status': response.status_code, 'ok': response.ok}
    except requests.RequestException as error:
        return {'url': url, 'status': type(error).__name__, 'ok': False}


if __name__ == '__main__':
    import json
    urls = sorted({url for r in load_catalog().records for url in [r.source_url, *[s['url'] for s in r.related_sources]]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, urls))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(r['ok'] for r in results) else 1)
