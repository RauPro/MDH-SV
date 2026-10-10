import pytest

@pytest.fixture(autouse=True)
def no_external_ai(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
