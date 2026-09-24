import pytest
from fastapi.testclient import TestClient
from venice_key_manager.web.app import app
from venice_key_manager.state import state_store

client = TestClient(app)


def test_health_endpoint():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["app"] == "venice-key-manager"


def test_unauthenticated_requests_fail():
    res = client.get("/api/categories")
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]

    res_set = client.get("/api/settings")
    assert res_set.status_code == 401

    res_back = client.get("/api/backup/export")
    assert res_back.status_code == 401


def test_auth_verify_and_authenticated_access():
    # 1. Invalid token verification
    res_bad = client.post("/api/auth/verify", json={"token": "invalid_fake_token"})
    assert res_bad.status_code == 401

    # 2. Create valid Telegram token in state_store
    token = state_store.create_auth_token(created_by="test_tg_user")
    assert token.startswith("vkm_tg_")

    # 3. Verify valid token
    res_ok = client.post("/api/auth/verify", json={"token": token})
    assert res_ok.status_code == 200
    assert res_ok.json()["valid"] is True
    assert "vkm_auth_token" in res_ok.cookies

    # 4. Access protected endpoint via Bearer header
    headers = {"Authorization": f"Bearer {token}"}
    res_cat = client.get("/api/categories", headers=headers)
    assert res_cat.status_code == 200
    assert "categories" in res_cat.json()

    # 5. Access protected endpoint via URL query parameter (?token=...)
    res_set = client.get(f"/api/settings?token={token}")
    assert res_set.status_code == 200
    assert "global_threshold" in res_set.json()

    # 6. Access protected endpoint via cookie
    res_cookie = client.get("/api/backup/export", cookies={"vkm_auth_token": token})
    assert res_cookie.status_code == 200


def test_index_page_with_and_without_token():
    client.cookies.clear()
    # 1. No token / unpaired: Must return 401 Unauthorized and NOT load the service
    res_unauth = client.get("/")
    assert res_unauth.status_code == 401
    assert "Venice Control Plane Locked" in res_unauth.text
    assert "app-header" not in res_unauth.text  # Service markup MUST NOT be served!

    # 2. With valid ?token= in URL: Must return 200 OK and load the service
    token = state_store.create_auth_token(created_by="url_test")
    res_auth = client.get(f"/?token={token}")
    assert res_auth.status_code == 200
    assert "vkm_auth_token" in res_auth.cookies
    assert "app-header" in res_auth.text  # Service loaded!

    # 3. With valid ?key= in URL
    token2 = state_store.create_auth_token(created_by="key_test")
    res_key = client.get(f"/?key={token2}")
    assert res_key.status_code == 200
    assert "app-header" in res_key.text
