"""Load the same declarative wire examples used by the JavaScript contract tests."""
from copy import deepcopy
import json
from pathlib import Path


def load_cases(path):
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    assert set(fixture) == {"schema_version", "contract", "bases", "cases"}
    assert type(fixture["schema_version"]) is int and fixture["schema_version"] == 1
    assert isinstance(fixture["contract"], str) and fixture["contract"]
    assert isinstance(fixture["bases"], dict) and isinstance(fixture["cases"], list)
    names, results = set(), []
    for case in fixture["cases"]:
        assert set(case) == {"name", "base", "valid", "operations"}
        assert isinstance(case["name"], str) and case["name"] and case["name"] not in names
        assert type(case["valid"]) is bool and isinstance(case["operations"], list)
        names.add(case["name"])
        document = deepcopy(fixture["bases"][case["base"]])
        for operation in case["operations"]:
            kind, keys = operation["op"], operation["path"]
            assert kind in {"add", "remove", "replace"}
            assert set(operation) == ({"op", "path"} if kind == "remove" else {"op", "path", "value"})
            assert isinstance(keys, list) and all(type(key) in {str, int} for key in keys)
            if not keys:
                assert kind == "replace"
                document = deepcopy(operation["value"])
                continue
            target = document
            for key in keys[:-1]:
                assert isinstance(target, (dict, list))
                if isinstance(target, list):
                    assert type(key) is int and 0 <= key < len(target)
                target = target[key]
            key = keys[-1]
            assert isinstance(target, (dict, list))
            if isinstance(target, list):
                assert type(key) is int and 0 <= key <= len(target)
                if kind == "add":
                    target.insert(key, deepcopy(operation["value"]))
                    continue
                assert key < len(target)
            else:
                assert isinstance(key, str)
                assert (key in target) == (kind != "add")
            if kind == "remove":
                del target[key]
            else:
                target[key] = deepcopy(operation["value"])
        results.append((case["name"], case["valid"], document))
    assert results
    return results
