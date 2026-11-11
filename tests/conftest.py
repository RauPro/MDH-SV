import os
from pathlib import Path

import pytest

# Historical unit fixtures model the upstream installation. Keep those fixtures
# stable when a contributor configures a fork; dedicated instance integration
# tests explicitly supply their own MDH_INSTANCE_FILE to fresh subprocesses.
os.environ['MDH_INSTANCE_FILE'] = str(Path(__file__).resolve().parent / 'fixtures/upstream-instance.json')

@pytest.fixture(autouse=True)
def no_external_ai(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for name in ("MDH_LLM_FALLBACK_CONFIG", "MDH_CYCLE_HOURS", "MDH_SCOPE_REQUESTS_PER_CYCLE",
                 "MDH_SCOPE_BATCH_COMMITS", "MDH_SOURCE_FETCH_WORKERS"):
        monkeypatch.delenv(name, raising=False)
