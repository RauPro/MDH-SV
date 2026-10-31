"""Explicit public catalogue boundary. Never export the candidate incident database."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from .file_outputs import write_outputs

from .contracts.catalog import (
    T as T,
    _calendar as _calendar,
    _version as _version,
    _today as _today,
    EvidenceField as EvidenceField,
    DossierEvidence as DossierEvidence,
    DossierLocation as DossierLocation,
    Dossier as Dossier,
    Publication as Publication,
    Catalog as Catalog,
)

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "data/reviewed/publications.json"
PUBLIC = ROOT / "apps/web/public/data"
CATALOG_EXPORT_NAMES = ('catalog.json', 'records.geojson', 'records.csv', 'records.parquet', 'manifest.json')


def load_catalog(path=CATALOG):
    return Catalog.model_validate_json(Path(path).read_text(encoding="utf-8"))


def csv_safe(value):
    text = str(value) if value is not None else ""
    # Excel also interprets formulas after leading whitespace/control characters.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def build_catalog_exports(source_bytes):
    """Render every public export from the exact reviewed bytes, without writes."""
    if not isinstance(source_bytes, bytes):
        raise TypeError('Catalogue input must be encoded bytes')
    catalog = Catalog.model_validate_json(source_bytes)
    import pyarrow as pa
    import pyarrow.parquet as pq
    payload = catalog.model_dump(mode="json")
    payload["records"].sort(key=lambda r: (r["published_date"], r["id"]), reverse=True)
    records = payload["records"]
    files = {}
    files["catalog.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    geo = {"type": "FeatureCollection", "scope": catalog.scope, "features": [
        {"type": "Feature", "geometry": None, "properties": r} for r in records]}
    files["records.geojson"] = json.dumps(geo, ensure_ascii=False, indent=2).encode()
    flat = [{k: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
             if isinstance(v, dict) or (isinstance(v, list) and v and isinstance(v[0], dict))
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
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "hash_scope": "Exported catalogue bytes; does not authenticate source articles or reported events.",
                "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                          for name, content in files.items()}}
    files['manifest.json'] = json.dumps(manifest, indent=2).encode('utf-8')
    return manifest, files


def export_catalog(source=CATALOG, destination=PUBLIC):
    """Render before replacing; a failed or interrupted export can be retried."""
    source, destination = Path(source), Path(destination)
    if source.resolve() in {(destination / name).resolve() for name in CATALOG_EXPORT_NAMES}:
        raise ValueError('The reviewed catalogue must be separate from its exports')
    source_bytes = source.read_bytes()
    manifest, files = build_catalog_exports(source_bytes)
    write_outputs([(destination / name, files[name]) for name in CATALOG_EXPORT_NAMES],
                  expected={source: source_bytes})
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', type=Path, default=CATALOG,
                        help='Reviewed catalogue JSON; defaults to the checkout catalogue')
    parser.add_argument('--output', type=Path, default=PUBLIC,
                        help='Destination directory for the validated public export files')
    args = parser.parse_args()
    print(json.dumps(export_catalog(source=args.reviewed, destination=args.output), indent=2))


if __name__ == "__main__":
    main()
