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
