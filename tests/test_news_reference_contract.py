"""The same bibliography identity/privacy examples as Node and the browser."""
from pathlib import Path

import pytest
from contract_fixtures import load_cases
from mdh_pipeline.publishers import SOURCES
from mdh_pipeline.contracts.reference import validate_reference

CASES = load_cases(Path(__file__).parent / 'fixtures/contracts/news-reference.json')


@pytest.mark.parametrize('name,valid,document', CASES, ids=[row[0] for row in CASES])
def test_news_reference_contract(name, valid, document):
    source = next((source for source in SOURCES if source['id'] == document.get('source_id')), SOURCES[0])
    if valid:
        validate_reference(document, source)
    else:
        with pytest.raises(ValueError):
            validate_reference(document, source)
