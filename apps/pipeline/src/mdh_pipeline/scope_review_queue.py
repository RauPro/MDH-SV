"""Recheck legacy scope in bounded batches; persist private decisions, never dossiers."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import time

from .article_source import fetch_article
from .gemini_budget import BudgetConfig, GeminiBudget, QuotaDeferred
from .gemini_draft import GeminiDraftError
from .gemini_review import candidate_from_issue, pending_issues, validate_envelope
from .news_selection import _archive_index, validate_scope_review
from .news_scope import classify_scope_batch
from .review_queue import GitHubInbox


def recheck_scope(inbox, drafts, archive, *, api_key, model, budget, max_requests=2,
                  batch_size=8, max_seconds=720, excluded_ids=(), fetch=fetch_article,
                  classify=classify_scope_batch, clock=time.monotonic):
    if type(max_requests) is not int or not 1 <= max_requests <= 20 or not 1 <= batch_size <= 8:
        raise ValueError('invalid_scope_batch_limit')
    if type(max_seconds) is not int or not 60 <= max_seconds <= 1800:
        raise ValueError('invalid_scope_time_limit')
    if budget is None or model != budget.config.model:
        raise ValueError('scope_budget_required')
    budget.snapshot()
    budget.set_request_limit(min(max_requests, budget.config.batch_ceiling))
    references = _archive_index(archive)
    excluded = set(excluded_ids)
    report = dict(candidates=0, cached=0, sources_checked=0, source_unavailable=0,
                  source_changed=0, reviewed=0, relevant_sv=0, http_requests=0, stopped_reason=None)
    deadline = clock() + max_seconds
    batch, sizes = [], 0

    def submit():
        nonlocal batch, sizes
        if not batch:
            return
        results = classify(batch, api_key=api_key, model=model, budget=budget)
        for result, item in zip(results, batch, strict=True):
            # The classifier binds order and exact identities; revalidate at the storage boundary.
            validate_scope_review(result, reference=item['reference'], content_sha256=item['article']['content_sha256'])
            relative = f"scope-reviews/{result['candidate_id']}.json"
            endpoint = inbox.base + '/contents/' + relative
            previous = inbox.get_file(endpoint)
            payload = {'message': 'Store private grounded news scope reassessment',
                       'content': base64.b64encode((json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()).decode(),
                       'branch': 'main'}
            if previous is not None:
                payload['sha'] = previous['sha']
            inbox.call('PUT', endpoint, json=payload)
            report['reviewed'] += 1
            report['relevant_sv'] += result['classification'] == {'jurisdiction': 'SV', 'relevance': 'relevant'}
        batch, sizes = [], 0

    try:
        # Existing positive classifications are candidates for reassessment, never publication permission.
        for original in sorted(drafts, key=lambda d: (d.get('news_dossier', {}).get('published_date') or '', d.get('candidate_id', '')), reverse=True):
            if clock() >= deadline or budget.requests_this_run >= max_requests:
                report['stopped_reason'] = 'run_limit'
                break
            identifier = original.get('candidate_id')
            reference = references.get(identifier)
            if identifier in excluded or reference is None:
                continue
            try:
                validate_envelope(original, reference)
                news, extraction = original['news_dossier'], original['extraction']
                if (news['extraction_status'] != 'processed'
                        or news['classification']['jurisdiction'] != 'SV'
                        or news['classification']['relevance'] != 'relevant'
                        or news['classification'] != original.get('classification')
                        or extraction.get('prompt_version') == 'mdh-universal-news-grounded-8-relevance-scope'):
                    continue
                digest = extraction['content_sha256']
                if any(news.get(k) != reference[k] for k in ('source_url', 'publisher', 'published_date')):
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            report['candidates'] += 1
            saved = inbox.get_file(inbox.base + f'/contents/scope-reviews/{identifier}.json')
            if saved is not None:
                try:
                    if saved.get('encoding') != 'base64' or not 0 < saved.get('size', 0) <= 30_000:
                        raise ValueError('invalid_scope_file')
                    prior = json.loads(base64.b64decode(saved['content']))
                    validate_scope_review(prior, reference=reference, content_sha256=digest)
                except (ValueError, TypeError, KeyError):
                    raise ValueError('invalid_saved_scope_review') from None
                report['cached'] += 1
                continue
            report['sources_checked'] += 1
            try:
                article = fetch({**reference, 'source_title': reference['title']})
            except Exception:
                report['source_unavailable'] += 1
                continue
            if article.get('content_sha256') != digest:
                report['source_changed'] += 1
                continue
            size = len(json.dumps(article['paragraphs'], ensure_ascii=False).encode())
            if batch and (len(batch) >= batch_size or sizes + size > 100_000):
                submit()
                if clock() >= deadline or budget.requests_this_run >= max_requests:
                    report['stopped_reason'] = 'run_limit'
                    break
            batch.append({'reference': reference, 'article': article})
            sizes += size
        if batch and clock() < deadline and budget.requests_this_run < max_requests:
            submit()
    except QuotaDeferred as error:
        report['stopped_reason'] = 'budget_deferred'
        report['quota_reason'] = error.reason
    except GeminiDraftError as error:
        report['stopped_reason'] = 'model_error'
        report['error_code'] = error.code
    report['http_requests'] = budget.requests_this_run
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drafts', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--max-requests', type=int, default=2)
    parser.add_argument('--max-seconds', type=int, default=720)
    args = parser.parse_args()
    try:
        inbox = GitHubInbox(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
        config = BudgetConfig(**json.loads(os.environ['MDH_GEMINI_BUDGET_CONFIG']))
        budget = GeminiBudget(inbox, config)
        pending = {candidate_from_issue(i)['id'] for i in pending_issues(inbox)}
        files = sorted(args.drafts.glob('*.json'))
        if args.drafts.is_symlink() or not args.drafts.is_dir() or len(files) > 50_000:
            raise ValueError('invalid_scope_drafts')
        if any(p.is_symlink() or p.stat().st_size > 512_000 for p in files):
            raise ValueError('invalid_scope_draft_file')
        drafts = [json.loads(p.read_text()) for p in files]
        excluded = {d.get('candidate_id') for d in drafts} - pending
        report = recheck_scope(inbox, drafts, json.loads(args.archive.read_text()),
            api_key=os.environ['GEMINI_API_KEY'], model=config.model, budget=budget,
            max_requests=args.max_requests, max_seconds=args.max_seconds, excluded_ids=excluded)
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'publications': 0}))
        raise SystemExit(1) from None
    print(json.dumps(report))
    if report['stopped_reason'] == 'model_error':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
