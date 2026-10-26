"""Cloudflare Access 標頭信任：Access 已改 Bypass（匿名可進）後，cf-access-* 標頭
由任何經 tunnel 的請求都能自帶，不再是可信來源 → 預設不得據此認定站主。
只有明確設 SCOPELAB_TRUST_CF_ACCESS=1（仍在 Access 後的過渡部署）才採信。"""
import pytest

from conftest import FakeRequest, OWNER
from src import auth

HDR = {"cf-access-authenticated-user-email": OWNER}


def test_cf_header_not_trusted_by_default(monkeypatch):
    monkeypatch.setattr(auth, "_TRUST_CF_ACCESS", False)
    req = FakeRequest(headers=HDR)
    assert auth.identity_email(req) is None
    assert not auth.is_owner(req)
    assert auth.public_locked(req)


def test_cf_header_trusted_only_when_opted_in(monkeypatch):
    monkeypatch.setattr(auth, "_TRUST_CF_ACCESS", True)
    req = FakeRequest(headers=HDR)
    assert auth.identity_email(req) == OWNER
    assert auth.is_owner(req)


def test_signed_cookie_still_wins_regardless(monkeypatch):
    monkeypatch.setattr(auth, "_TRUST_CF_ACCESS", False)
    req = FakeRequest(cookies={auth.COOKIE_NAME: auth.make_session(OWNER)})
    assert auth.is_owner(req)
