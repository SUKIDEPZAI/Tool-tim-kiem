import os
import tempfile

import pytest

import app as module


@pytest.fixture()
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(module, "DB_PATH", path)
    module.app.config.update(TESTING=True)
    with module.app.test_client() as client:
        yield client
    os.unlink(path)


def make_key(client):
    response = client.post("/api/keys", json={"name": "test"})
    assert response.status_code == 201
    return response.get_json()["api_key"]


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_key_is_required(client):
    response = client.get("/api/search?q=python")
    assert response.status_code == 401


def test_search_validation(client):
    key = make_key(client)
    response = client.get("/api/search", headers={"X-API-Key": key})
    assert response.status_code == 400


def test_search_aggregates_results(client, monkeypatch):
    key = make_key(client)
    def fake_ddg(q, limit):
        return [{"title": "Python docs", "url": "https://python.org/docs", "snippet": "Python documentation", "source": "DuckDuckGo"}]
    def fake_wiki(q, limit):
        return [{"title": "Python", "url": "https://en.wikipedia.org/wiki/Python", "snippet": "Python language", "source": "Wikipedia"}]
    def fake_news(q, limit):
        return [{"title": "Python docs", "url": "https://python.org/docs", "snippet": "Duplicate", "source": "Google News"}]
    monkeypatch.setattr(module, "search_duckduckgo", fake_ddg)
    monkeypatch.setattr(module, "search_wikipedia", fake_wiki)
    monkeypatch.setattr(module, "search_google_news", fake_news)
    response = client.get("/api/search?q=python&limit=5", headers={"X-API-Key": key})
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 2
    assert len(data["providers"]) == 3
