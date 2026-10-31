"""Universal news worksheets, independent of eligibility for the case catalogue.

Helpers perform no network or file access. An unreviewed worksheet is never a
publication approval, including when extraction completed without missing values.
"""
from __future__ import annotations


from .contracts.catalog import Publication as Publication

from .contracts.dossier import (
    T as T,
    ExtractionStatus as ExtractionStatus,
    FIELD_NAMES as FIELD_NAMES,
    LIST_FIELDS as LIST_FIELDS,
    MAX_DOCUMENT_BYTES as MAX_DOCUMENT_BYTES,
    _today as _today,
    _source_url as _source_url,
    NewsDossierError as NewsDossierError,
    NewsField as NewsField,
    NewsFields as NewsFields,
    NewsClassification as NewsClassification,
    NewsEvidence as NewsEvidence,
    NewsDossier as NewsDossier,
    validate_news_dossier as validate_news_dossier,
    validate_published_news_dossier as validate_published_news_dossier,
    empty_news_dossier as empty_news_dossier,
    from_case_record as from_case_record,
    from_legacy_record as from_legacy_record,
)
