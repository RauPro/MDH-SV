"""Shared JSON file operations with explicit, preserved domain policies.

The processing reader rejects duplicate keys/non-finite constants and unsafe files.
Other domains retain their own readers; this module does not broaden their trust.
"""
import json
import tempfile
from pathlib import Path
from .contracts.processing import NewsProcessingError

def read_processing_json(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise NewsProcessingError("invalid_processing_file")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise NewsProcessingError("invalid_processing_file")
            result[key] = value
        return result

    def invalid_constant(_):
        raise NewsProcessingError("invalid_processing_file")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def write_json_atomic(path, document, *, prefix=".processing-"):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=prefix, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_processing_json(path, document):
    write_json_atomic(path, document, prefix=".processing-")


def write_location_json(path, document):
    write_json_atomic(path, document, prefix=".locations-")
