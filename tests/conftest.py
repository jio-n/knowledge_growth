"""API tests use a fresh temporary data directory and the offline mock provider."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KG_LLM_PROVIDER", "mock")
    with TestClient(create_app()) as test_client:
        yield test_client
