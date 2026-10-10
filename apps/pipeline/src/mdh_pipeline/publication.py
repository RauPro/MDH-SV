"""Explicit public catalogue boundary. Never export the candidate incident database."""
import csv
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "data/reviewed/publications.json"
PUBLIC = ROOT / "apps/web/public/data"


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
    human_reviewed: bool
    reviewed_by: str | None = None
    reviewed_at: date | None = None
    review_notes: str | None = None
    origin_candidate_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    related_sources: list[dict[str, str]] = Field(default_factory=list)
    reported_actor: str | None
    source_record_key: str = Field(min_length=4)
    privacy_note: str = Field(min_length=10)
    penitentiary_related: bool

    @field_validator("source_url")
    @classmethod
    def primary_source(cls, value):
        url = urlparse(value)
        if (url.scheme != "https" or url.hostname not in
                {"www.hrw.org", "www.oas.org", "www.amnesty.org", "citizenlab.ca",
                 "www.corteidh.or.cr", "spcommreports.ohchr.org", "www.ohchr.org",
                 "www.diariocolatino.com", "diariocolatino.com", "gatoencerrado.news",
                 "www.revistafactum.com", "revistafactum.com"}
                or url.username or url.password or url.port or len(url.path) < 5):
            raise ValueError("Expected an explicit, reviewed primary-source HTTPS URL")
        return value

    @model_validator(mode="after")
    def dates(self):
        if self.human_reviewed:
            if (self.review_status != 'editorially_reviewed' or not self.reviewed_by
                    or not self.reviewed_at or len(self.review_notes or '') < 20):
                raise ValueError('Human review requires reviewer, date and substantive notes')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', self.reviewed_by):
                raise ValueError('Invalid reviewer identifier')
            if self.reviewed_at > date.today():
                raise ValueError('Future review date')
        elif (self.review_status != 'source_checked' or self.reviewed_by or self.reviewed_at or self.review_notes):
            raise ValueError('Do not attribute a human review that did not take place')
        if self.origin_candidate_id and not self.human_reviewed:
            raise ValueError('Collected candidates require explicit human review before publication')
        for source in self.related_sources:
            if set(source) != {'url', 'title', 'published_date', 'locator'} or not all(source.values()):
                raise ValueError('Invalid supporting source')
            self.primary_source(source['url'])
            if date.fromisoformat(source['published_date']) > self.source_checked_at:
                raise ValueError('Supporting source newer than review')
        if self.published_date > self.source_checked_at or self.source_checked_at > date.today():
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

    @model_validator(mode="after")
    def unique(self):
        for key in ("id", "source_record_key"):
            values = [getattr(r, key) for r in self.records]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {key}")
        if any(r.source_checked_at > self.updated_at for r in self.records):
            raise ValueError("Catalogue older than source review")
        return self


def load_catalog(path=CATALOG):
    return Catalog.model_validate_json(Path(path).read_text(encoding="utf-8"))


def csv_safe(value):
    text = str(value) if value is not None else ""
    # Excel also interprets formulas after leading whitespace/control characters.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def export_catalog(source=CATALOG, destination=PUBLIC):
    import pyarrow as pa
    import pyarrow.parquet as pq
    catalog = load_catalog(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    payload = catalog.model_dump(mode="json")
    payload["records"].sort(key=lambda r: (r["published_date"], r["id"]), reverse=True)
    records = payload["records"]
    files = {}
    files["catalog.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    geo = {"type": "FeatureCollection", "scope": catalog.scope, "features": [
        {"type": "Feature", "geometry": None, "properties": r} for r in records]}
    files["records.geojson"] = json.dumps(geo, ensure_ascii=False, indent=2).encode()
    flat = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) and v and isinstance(v[0], dict)
             else " | ".join(v) if isinstance(v, list) else v for k, v in r.items()
             if k != "geometry"} for r in records]
    stream = io.StringIO(newline="")
    fields = list(Publication.model_fields)
    fields.remove("geometry")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({k: csv_safe(v) for k, v in r.items()} for r in flat)
    files["records.csv"] = stream.getvalue().encode("utf-8-sig")
    schema = pa.schema([(key, pa.bool_() if key in ('human_reviewed', 'penitentiary_related') else pa.string()) for key in fields])
    table = pa.Table.from_pylist(flat, schema=schema)
    buffer = pa.BufferOutputStream()
    pq.write_table(table, buffer, compression="snappy")
    files["records.parquet"] = buffer.getvalue().to_pybytes()
    manifest = {"schema_version": 1, "updated_at": payload["updated_at"],
                "records_count": len(records),
                "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                "hash_scope": "Exported catalogue bytes; does not authenticate source articles or reported events.",
                "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                          for name, content in files.items()}}
    # Only fixed allowlisted filenames are written; raw/private data never enter the web directory.
    for name, content in files.items():
        temp = destination / (name + ".tmp")
        temp.write_bytes(content)
        temp.replace(destination / name)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(export_catalog(), indent=2))
