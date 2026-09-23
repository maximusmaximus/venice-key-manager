import pytest
from fastapi.testclient import TestClient
from venice_key_manager.web.app import app

client = TestClient(app)


def test_health_endpoint():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["app"] == "venice-key-manager"


def test_categories_endpoint():
    res = client.get("/api/categories")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "Default" in data["categories"]


def test_settings_endpoint():
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert "global_threshold" in data
    assert "categories" in data


def test_backup_export_endpoint():
    res = client.get("/api/backup/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    data = res.json()
    assert "global_threshold" in data
    assert "categories" in data
