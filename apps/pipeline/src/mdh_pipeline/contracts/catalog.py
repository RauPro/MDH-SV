"""Pure catalog contracts, extracted without changing validation or normalization."""
import re
from datetime import date, datetime, timezone
from typing import Generic, Literal, TypeVar, get_args
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..publishers import PRIMARY_SOURCE_HOSTS

T = TypeVar("T")

def _calendar(value):
    if value is None or type(value) is date:
        return value
    if (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            or date.fromisoformat(value).isoformat() != value):
        raise ValueError("Expected an explicit ISO calendar date")
    return value

def _version(value):
    if type(value) not in {int, float} or value != 1:
        raise ValueError("Expected public schema version 1")
    return 1

def _today():
    # Catalogue review dates use one clock in Python, Node and the browser.
    return datetime.now(timezone.utc).date()

class EvidenceField(BaseModel, Generic[T]):
    """A source-attributed value, never a determination that an allegation is proven."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    value: T | None
    status: Literal["reported", "not_reported", "withheld", "not_applicable"]
    evidence_refs: list[str] = Field(default_factory=list)
    note: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def explicit_calendar_value(cls, value):
        return _calendar(value) if date in get_args(cls.model_fields["value"].annotation) else value

    @model_validator(mode="after")
    def meaningful_value(self):
        if self.status == "reported":
            if self.value is None or self.value == "" or self.value == [] or not self.evidence_refs:
                raise ValueError("Reported dossier values require a value and evidence references")
            if isinstance(self.value, list) and any(not item.strip() for item in self.value):
                raise ValueError("Dossier lists cannot contain blank values")
        elif self.value is not None:
            raise ValueError("Missing or withheld dossier fields must have a null value")
        if self.status in ("withheld", "not_applicable") and len(self.note or "") < 10:
            raise ValueError("Withheld or inapplicable dossier fields require an explanation")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate evidence reference")
        return self

class DossierEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    url: str
    locator: str = Field(min_length=10)

class DossierLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department: EvidenceField[str]
    municipality: EvidenceField[str]
    public_place: EvidenceField[str]

class Dossier(BaseModel):
    """Public, minimised research fields derived from the original WSDS indicators."""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    alleged_violation: EvidenceField[str]
    facts: EvidenceField[str]
    affected_persons: EvidenceField[list[str]]
    location: DossierLocation
    event_date: EvidenceField[date]
    event_date_end: EvidenceField[date]
    date_precision: Literal["day", "unknown", "month_range", "year_range"]
    event_time: EvidenceField[str]
    reported_actors: EvidenceField[list[str]]
    institutional_sources: EvidenceField[list[str]]
    vulnerable_groups: EvidenceField[list[str]]
    themes: EvidenceField[list[str]]
    hypothesis: EvidenceField[str]
    instrument: EvidenceField[list[str]]
    evidence: list[DossierEvidence] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def explicit_version(cls, value):
        return _version(value)

    def evidence_fields(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, EvidenceField):
                yield name, value
        for name in DossierLocation.model_fields:
            yield "location." + name, getattr(self.location, name)

    @model_validator(mode="after")
    def valid_references(self):
        ids = [source.id for source in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate dossier evidence identifier")
        for _, field in self.evidence_fields():
            if not set(field.evidence_refs) <= set(ids):
                raise ValueError("Dossier field references missing evidence")
        return self

class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    record_type: Literal["publication", "incident", "aggregate"]
    title: str = Field(min_length=3, max_length=300)
    publisher: str = Field(min_length=3)
    source_title: str = Field(min_length=3)
    published_date: date
    date_note: str = Field(min_length=10)
    category: str = Field(min_length=3)
    tags: list[str] = Field(min_length=1)
    summary: str = Field(min_length=20, max_length=1000)
    source_url: str
    source_locator: str = Field(min_length=10)
    source_checked_at: date
    source_check_method: str = Field(min_length=20)
    coverage: str
    department: str | None
    geometry: None
    language: Literal["es"]
    incident_date: date | None
    incident_date_end: date | None
    date_precision: Literal["day", "unknown", "month_range", "year_range"]
    review_status: Literal["source_checked", "editorially_reviewed"]
    human_reviewed: bool = Field(strict=True)
    reviewed_by: str | None = None
    reviewed_at: date | None = None
    review_notes: str | None = None
    origin_candidate_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    related_sources: list[dict[str, str]] = Field(default_factory=list)
    reported_actor: str | None
    source_record_key: str = Field(min_length=4)
    privacy_note: str = Field(min_length=10)
    penitentiary_related: bool = Field(strict=True)
    dossier: Dossier

    @field_validator("published_date", "source_checked_at", "incident_date", "incident_date_end",
                     "reviewed_at", mode="before")
    @classmethod
    def explicit_dates(cls, value):
        return _calendar(value)

    @field_validator("source_url")
    @classmethod
    def primary_source(cls, value):
        url = urlparse(value)
        if (url.scheme != "https" or url.hostname not in PRIMARY_SOURCE_HOSTS
                or url.username or url.password or url.port or len(url.path) < 5
                or re.search(r"[\x00-\x20\x7f\\]", value) or "@" in url.netloc
                or url.netloc.lower() != url.hostname):
            raise ValueError("Expected an explicit, reviewed primary-source HTTPS URL")
        return value

    @model_validator(mode="after")
    def dates(self):
        allowed_evidence = {self.source_url, *(source.get('url') for source in self.related_sources)}
        for evidence in self.dossier.evidence:
            self.primary_source(evidence.url)
            if evidence.url not in allowed_evidence:
                raise ValueError("Dossier evidence must refer to a record's declared sources")
        if (self.dossier.event_date.value != self.incident_date
                or self.dossier.event_date_end.value != self.incident_date_end
                or self.dossier.date_precision != self.date_precision
                or self.dossier.location.department.value != self.department):
            raise ValueError("Dossier dates and department must match the public record")
        if self.record_type == "publication":
            event_fields = (self.dossier.event_date, self.dossier.event_date_end,
                            self.dossier.event_time, self.dossier.location.department,
                            self.dossier.location.municipality, self.dossier.location.public_place)
            if any(field.status != "not_applicable" for field in event_fields):
                raise ValueError("An overview publication must not be located or timed as a single event")
        if self.human_reviewed:
            if (self.review_status != 'editorially_reviewed' or not self.reviewed_by
                    or not self.reviewed_at or len(self.review_notes or '') < 20):
                raise ValueError('Human review requires reviewer, date and substantive notes')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', self.reviewed_by):
                raise ValueError('Invalid reviewer identifier')
            if self.reviewed_at > _today() or self.reviewed_at < self.source_checked_at:
                raise ValueError('Inconsistent review date')
        elif (self.review_status != 'source_checked' or self.reviewed_by or self.reviewed_at or self.review_notes):
            raise ValueError('Do not attribute a human review that did not take place')
        if self.origin_candidate_id and not self.human_reviewed:
            raise ValueError('Collected candidates require explicit human review before publication')
        for source in self.related_sources:
            if set(source) != {'url', 'title', 'published_date', 'locator'} or not all(source.values()):
                raise ValueError('Invalid supporting source')
            self.primary_source(source['url'])
            _calendar(source['published_date'])
            if date.fromisoformat(source['published_date']) > self.source_checked_at:
                raise ValueError('Supporting source newer than review')
        if self.published_date > self.source_checked_at or self.source_checked_at > _today():
            raise ValueError("Publication/review dates are inconsistent")
        if self.incident_date and self.incident_date > self.published_date:
            raise ValueError("Event cannot be later than its source")
        if self.incident_date_end and (not self.incident_date or self.incident_date_end < self.incident_date or self.incident_date_end > self.published_date):
            raise ValueError("Invalid event interval")
        if self.record_type == "publication" and (self.incident_date or self.department):
            raise ValueError("Do not locate or date a publication as an incident")
        if (self.date_precision == "unknown") != (self.incident_date is None):
            raise ValueError("Date precision must reflect available evidence")
        if self.date_precision in ("month_range", "year_range") and self.incident_date_end is None:
            raise ValueError("A range requires an end date")
        if self.department:
            from mdh_schema import DepartmentEnum
            DepartmentEnum(self.department)
        return self

class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    updated_at: date
    scope: str
    records: list[Publication]

    @field_validator("schema_version", mode="before")
    @classmethod
    def explicit_version(cls, value):
        return _version(value)

    @field_validator("updated_at", mode="before")
    @classmethod
    def explicit_date(cls, value):
        return _calendar(value)

    @model_validator(mode="after")
    def unique(self):
        for key in ("id", "source_record_key"):
            values = [getattr(r, key) for r in self.records]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {key}")
        if any(r.source_checked_at > self.updated_at for r in self.records):
            raise ValueError("Catalogue older than source review")
        return self
