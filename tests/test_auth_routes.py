"""/auth/google 與 /auth/logout 的行為與 CSRF：
- dummy credential → 401（不是 422；owner-mode skill 的自測）。
- 跨站 Origin（含其他 *.tinghaochang.com 子網域）POST 進來 → 403，擋掉 CSRF。
- 同源 / 無 Origin（app 自己的 fetch）→ 照常。
"""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from src import auth


@pytest.fixture()
def client():
    app = FastAPI()
    auth.add_auth_routes(app)
    return TestClient(app)


def test_google_dummy_is_401_not_422(client):
    r = client.post("/auth/google", json={"credential": "x.y"})
    assert r.status_code == 401, r.text


def test_logout_cross_origin_rejected(client):
    r = client.post("/auth/logout", headers={"Origin": "https://clinscope.tinghaochang.com"})
    assert r.status_code == 403


def test_google_cross_origin_rejected(client):
    r = client.post("/auth/google", json={"credential": "x.y"},
                    headers={"Origin": "https://evil.tinghaochang.com"})
    assert r.status_code == 403


def test_logout_same_origin_ok(client):
    # TestClient 的 Host 是 testserver；同源 Origin 相符 → 放行
    r = client.post("/auth/logout", headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_logout_no_origin_ok(client):
    r = client.post("/auth/logout")
    assert r.status_code == 200


def test_google_error_detail_not_leaked(client, monkeypatch):
    """例外內文（預期的 client id、時鐘差、伺服器路徑）只記在伺服器端，回應只給通用訊息（審查 GA-10）。"""
    def boom(_cred):
        raise ValueError("Token has wrong audience secret-client-id.apps, expected D:/Claude code/x")
    monkeypatch.setattr(auth, "verify_google_credential", boom)
    r = client.post("/auth/google", json={"credential": "x.y.z"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid credential"}
    assert "secret-client-id" not in r.text and "Claude code" not in r.text


def test_public_mount_hides_unexpected_error_detail(server):
    """mount_gradio_app 用 show_error=False：非預期例外內文不回給瀏覽器（gr.Error 照常顯示）。"""
    assert server.blocks.show_error is False
