"""Declarative public-wire cases also exercised by Node and the frontend."""
from copy import deepcopy
from pathlib import Path

import pytest
from contract_fixtures import load_cases
from mdh_pipeline.publication import Catalog

CASES = load_cases(Path(__file__).parent / 'fixtures/contracts/catalogue.json')


@pytest.mark.parametrize('name,valid,document', CASES, ids=[row[0] for row in CASES])
def test_public_catalogue_contract(name, valid, document):
    original = deepcopy(document)
    if valid:
        catalog = Catalog.model_validate(document)
        assert len(catalog.records) == len(document['records'])
    else:
        with pytest.raises(ValueError):
            Catalog.model_validate(document)
    assert document == original
