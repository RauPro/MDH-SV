"""Universal news worksheets, independent of eligibility for the case catalogue.

Helpers perform no network or file access. An unreviewed worksheet is never a
publication approval, including when extraction completed without missing values.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .publication import Publication
from .publishers import NEWS_HOSTS
from .review_queue import canonical_url

T = TypeVar("T")
ExtractionStatus = Literal["pending", "processed", "source_unavailable", "error"]
FIELD_NAMES = (
    "summary", "category", "alleged_violation", "facts", "affected_persons", "country",
    "department", "municipality", "public_place", "event_date", "event_date_end", "event_time",
    "reported_actors", "institutional_sources", "vulnerable_groups", "themes", "hypothesis", "instrument",
)
LIST_FIELDS = {"affected_persons", "reported_actors", "institutional_sources", "vulnerable_groups", "themes", "instrument"}
MAX_DOCUMENT_BYTES = 180_000


def _today():
    return datetime.now(ZoneInfo("America/El_Salvador")).date()


def _source_url(value):
    Publication.primary_source(value)
    return canonical_url(value) if urlsplit(value).hostname in NEWS_HOSTS else value


class NewsDossierError(ValueError):
    def __init__(self, code="invalid_news_dossier"):
        self.code = code
        super().__init__(code)


class NewsField(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    value: T | None
    status: Literal["pending", "reported", "not_reported", "withheld", "not_applicable"]
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def supported(self):
        if self.status == "reported":
            if self.value is None or self.value == "" or self.value == [] or not self.evidence_refs:
                raise ValueError("Reported fields require a value and evidence")
            if isinstance(self.value, list) and any(not v.strip() for v in self.value):
                raise ValueError("Blank list values are not claims")
            if isinstance(self.value, str) and len(self.value) > 2000:
                raise ValueError("Field text exceeds its bound")
            if isinstance(self.value, list) and (len(self.value) > 40 or any(len(v) > 300 for v in self.value)):
                raise ValueError("Field list exceeds its bound")
        elif self.value is not None or self.evidence_refs:
            raise ValueError("Non-reported fields must be null without claim references")
        if self.status in {"withheld", "not_applicable"} and len(self.note or "") < 10:
            raise ValueError("Protected or inapplicable fields require an explanation")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate evidence references")
        return self


class NewsFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: NewsField[str]
    category: NewsField[str]
    alleged_violation: NewsField[str]
    facts: NewsField[str]
    affected_persons: NewsField[list[str]]
    country: NewsField[str]
    department: NewsField[str]
    municipality: NewsField[str]
    public_place: NewsField[str]
    event_date: NewsField[str]
    event_date_end: NewsField[str]
    event_time: NewsField[str]
    reported_actors: NewsField[list[str]]
    institutional_sources: NewsField[list[str]]
    vulnerable_groups: NewsField[list[str]]
    themes: NewsField[list[str]]
    hypothesis: NewsField[str]
    instrument: NewsField[list[str]]


class NewsClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction: Literal["SV", "other", "unclear"]
    relevance: Literal["relevant", "not_relevant", "unclear"]
    record_type: Literal["incident", "aggregate", "publication"] | None


class NewsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    url: str = Field(max_length=4096)
    locator: str = Field(min_length=10, max_length=1000)


class NewsDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal[1]
    kind: Literal["news_dossier"]
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str = Field(max_length=4096)
    publisher: str = Field(min_length=3, max_length=200)
    source_title: str = Field(min_length=3, max_length=500)
    published_date: date | None
    extraction_status: ExtractionStatus
    classification: NewsClassification
    fields: NewsFields
    date_precision: Literal["day", "unknown", "month_range", "year_range"]
    evidence: list[NewsEvidence] = Field(min_length=1, max_length=200)
    human_reviewed: bool = Field(strict=True)
    reviewed_by: str | None
    reviewed_at: date | None
    review_notes: str | None = Field(max_length=4000)

    @model_validator(mode="after")
    def integrity(self):
        if _source_url(self.source_url) != self.source_url:
            raise ValueError("Expected a canonical source URL")
        if hashlib.sha256(self.source_url.encode()).hexdigest() != self.candidate_id:
            raise ValueError("Candidate identity does not match source URL")
        ids = [item.id for item in self.evidence]
        if len(ids) != len(set(ids)) or not any(item.url == self.source_url for item in self.evidence):
            raise ValueError("Missing primary evidence or duplicate evidence identifiers")
        for item in self.evidence:
            Publication.primary_source(item.url)
        for name in FIELD_NAMES:
            field = getattr(self.fields, name)
            if not set(field.evidence_refs) <= set(ids):
                raise ValueError("Missing field evidence")
            if self.extraction_status != "processed" and field.status not in {"pending", "withheld"}:
                raise ValueError("Unprocessed documents cannot assert article contents or absence")
        start, end = self.fields.event_date.value, self.fields.event_date_end.value
        for value in (start, end):
            if value is not None and (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or date.fromisoformat(value).isoformat() != value):
                raise ValueError("Invalid event date")
        if (self.date_precision == "unknown") != (start is None):
            raise ValueError("Date precision contradicts the event date")
        if end and (not start or end < start):
            raise ValueError("Inconsistent event interval")
        if self.date_precision in {"month_range", "year_range"} and not end:
            raise ValueError("Date range requires its end")
        if self.published_date and self.published_date > _today():
            raise ValueError("Publication date is in the future")
        if self.human_reviewed:
            if (self.extraction_status != "processed" or not self.reviewed_by
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", self.reviewed_by)
                    or not self.reviewed_at or self.reviewed_at > _today()
                    or (self.published_date and self.reviewed_at < self.published_date)
                    or len(self.review_notes or "") < 20):
                raise ValueError("Human review requires identity, date and substantive notes")
        elif self.reviewed_by or self.reviewed_at or self.review_notes:
            raise ValueError("Do not attribute review metadata without human review")
        return self


def validate_news_dossier(document, require_reviewed=False):
    """Validate and return plain JSON data; failures never echo personal values."""
    try:
        if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
                or type(document.get("human_reviewed")) is not bool
                or len(json.dumps(document, ensure_ascii=False).encode()) > MAX_DOCUMENT_BYTES):
            raise ValueError("Invalid document envelope")
        for key in ("published_date", "reviewed_at"):
            value = document.get(key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                raise ValueError("Expected an explicit ISO calendar date")
        result = NewsDossier.model_validate(document)
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise NewsDossierError() from None
    if require_reviewed and not result.human_reviewed:
        raise NewsDossierError("news_dossier_review_required")
    return result.model_dump(mode="json")


def empty_news_dossier(candidate, extraction_status="pending"):
    """Every valid bibliographic reference has a worksheet, even without a body."""
    try:
        url = _source_url(candidate["source_url"])
        identifier = candidate.get("candidate_id") or candidate.get("id")
        if not identifier:
            identifier = hashlib.sha256(url.encode()).hexdigest()
        note = {"pending": "Campo pendiente de evaluación; el texto todavía no se ha analizado.",
                "processed": "Campo pendiente de evaluación en esta ficha.",
                "source_unavailable": "Fuente no disponible para extracción; no se ha evaluado este campo.",
                "error": "La extracción no se completó; no se ha evaluado este campo."}[extraction_status]
        document = {
            "schema_version": 1, "kind": "news_dossier", "candidate_id": identifier, "source_url": url,
            "publisher": candidate["publisher"], "source_title": candidate.get("source_title") or candidate.get("title"),
            "published_date": candidate.get("published_date"), "extraction_status": extraction_status,
            "classification": {"jurisdiction": "unclear", "relevance": "unclear", "record_type": None},
            "fields": {name: {"value": None, "status": "pending", "evidence_refs": [], "note": note} for name in FIELD_NAMES},
            "date_precision": "unknown", "evidence": [{"id": "primary", "url": url,
                "locator": "Referencia bibliográfica del medio; los metadatos no acreditan los campos del texto."}],
            "human_reviewed": False, "reviewed_by": None, "reviewed_at": None, "review_notes": None,
        }
        return validate_news_dossier(document)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsDossierError() from None


def from_case_record(document):
    """Migrate a prior case without inventing an absent field or approving its news worksheet."""
    try:
        envelope = document if isinstance(document.get("record"), dict) else {}
        record = envelope.get("record", document)
        candidate = {"candidate_id": envelope.get("candidate_id") or record.get("origin_candidate_id"),
                     "source_url": record["source_url"], "publisher": record["publisher"],
                     "source_title": record["source_title"], "published_date": record["published_date"]}
        result = empty_news_dossier(candidate, extraction_status="processed")
        result["classification"]["record_type"] = record["record_type"]
        for name in ("jurisdiction", "relevance"):
            value = envelope.get("classification", {}).get(name)
            if value in ({"SV", "other", "unclear"} if name == "jurisdiction" else {"relevant", "not_relevant", "unclear"}):
                result["classification"][name] = value
        dossier = record["dossier"]
        result["evidence"] = [dict(item) for item in dossier["evidence"]]
        if not any(item["url"] == result["source_url"] for item in result["evidence"]):
            raise ValueError("Missing original source")
        primary = next(item["id"] for item in result["evidence"] if item["url"] == result["source_url"])
        for name in FIELD_NAMES:
            if name in {"summary", "category"}:
                value = record.get(name)
                if value and not value.startswith(("Borrador", "Pendiente")):
                    refs = dossier.get("facts", {}).get("evidence_refs") or [primary]
                    result["fields"][name] = {"value": value, "status": "reported", "evidence_refs": refs,
                        "note": "Valor heredado de la ficha previa; requiere revisión de esta ficha de noticia."}
                continue
            field = dossier.get("location", {}).get(name) if name in {"department", "municipality", "public_place"} else dossier.get(name)
            if not isinstance(field, dict):
                continue
            if field.get("status") == "not_reported":
                result["fields"][name]["note"] = "El registro anterior no consignaba este dato; no se ha evaluado si aparece en el texto."
            else:
                result["fields"][name] = dict(field)
                if field.get("status") in {"withheld", "not_applicable", "pending"}:
                    result["fields"][name].update(value=None, evidence_refs=[])
        result["date_precision"] = dossier["date_precision"]
        return validate_news_dossier(result)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsDossierError() from None


from_legacy_record = from_case_record
