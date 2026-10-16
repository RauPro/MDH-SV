"""Same non-secret configuration corpus as the browser and API validators."""
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from mdh_pipeline.instance import DEFAULT_PATH, InstanceConfigurationError, load_instance

CASES = json.loads((Path(__file__).parent / 'fixtures/instance-config-cases.json').read_text())


@pytest.mark.parametrize('case', CASES['cases'], ids=lambda case: case['name'])
def test_shared_instance_contract(case, tmp_path):
    value = {**deepcopy(CASES['base']), **case.get('patch', {})}
    for name in case.get('remove', []):
        del value[name]
    path = tmp_path / 'instance.json'
    path.write_text(case.get('raw', json.dumps(value)))
    if case['valid']:
        result = load_instance(path)
        assert result.public_repository == value['public_repository']
        assert not result.public_url.endswith('/')
        assert isinstance(result.mcp_allowed_hosts, tuple)
    else:
        with pytest.raises(InstanceConfigurationError, match='^invalid_instance_configuration$'):
            load_instance(path)


def test_default_is_approved_module_checkout_never_current_directory(tmp_path, monkeypatch):
    expected = load_instance(DEFAULT_PATH)
    (tmp_path / 'instance.json').write_text('{"GEMINI_API_KEY":"must-not-be-read"}')
    monkeypatch.chdir(tmp_path)
    assert load_instance(environ={}) == expected
    with pytest.raises(FrozenInstanceError):
        expected.public_repository = 'attacker/repo'


@pytest.mark.parametrize('path', ['', 'instance.json', '../instance.json'])
def test_override_must_be_explicit_absolute_path(path):
    with pytest.raises(InstanceConfigurationError, match='^invalid_instance_configuration$'):
        load_instance(environ={'MDH_INSTANCE_FILE': path})


def test_override_missing_symlink_oversized_and_non_utf8_fail_closed(tmp_path):
    invalid = tmp_path / 'malformed-instance.json'
    symlink = tmp_path / 'linked-instance.json'
    symlink.symlink_to(invalid)
    for raw in (None, b'\xff', b' ' * 32_769, b'{"unexpected":"PRIVATE_SENTINEL"}'):
        if raw is not None:
            invalid.write_bytes(raw)
        for path in (invalid, symlink, tmp_path):
            with pytest.raises(InstanceConfigurationError) as caught:
                load_instance(environ={'MDH_INSTANCE_FILE': str(path)})
            assert str(caught.value) == 'invalid_instance_configuration'
