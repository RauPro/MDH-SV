"""Public evidence contracts consume exactly the examples used by JavaScript."""
from pathlib import Path

import pytest
from contract_fixtures import load_cases
from mdh_pipeline.automatic_locations import validate_collection as locations
from mdh_pipeline.news_dossier import validate_published_news_dossier as dossier
from mdh_pipeline.news_processing import validate_collection as processing
from mdh_pipeline.news_selection import validate_selection as selection

FIXTURES = Path(__file__).parent / "fixtures/contracts"
VALIDATORS = {"news-selection": selection, "news-dossier": dossier,
              "news-processing": processing, "news-location": locations}
CASES = [pytest.param(validator, valid, document, id=f"{contract}: {name}")
         for contract, validator in VALIDATORS.items()
         for name, valid, document in load_cases(FIXTURES / f"{contract}.json")]


@pytest.mark.parametrize("validator,valid,document", CASES)
def test_public_wire_contract(validator, valid, document):
    if valid:
        validator(document)
    else:
        with pytest.raises(ValueError):
            validator(document)
