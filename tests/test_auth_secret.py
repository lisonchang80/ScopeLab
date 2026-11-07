"""公開站的 session 簽章金鑰：若沒設（用 repo 內公開的 dev 預設值 / 空字串），cookie 可被
任何人偽造成站主 → fail-closed：公開站不採信任何 cookie（_secret_ok 為假）。"""
import pytest

from conftest import FakeRequest, OWNER
from src import auth


@pytest.mark.parametrize("secret", ["dev-insecure-change-me", "", "   "])
def test_default_secret_rejects_cookies_on_public(monkeypatch, secret):
    monkeypatch.setattr(auth, "SESSION_SECRET", secret)
    assert not auth._secret_ok()
    forged = auth.make_session(OWNER)                       # 用（公開已知的）預設金鑰簽
    req = FakeRequest(cookies={auth.COOKIE_NAME: forged})
    assert auth.read_session(forged) is None                # cookie 一律不採信
    assert not auth.is_owner(req)
    assert auth.public_locked(req)


def test_real_secret_trusts_cookies_on_public(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", "a-real-long-random-secret-0123456789")
    auth._SER = None                                        # 讓序列化器用新金鑰重建
    try:
        assert auth._secret_ok()
        req = FakeRequest(cookies={auth.COOKIE_NAME: auth.make_session(OWNER)})
        assert auth.is_owner(req)
    finally:
        auth._SER = None


def test_default_secret_fine_offline(monkeypatch):
    """桌面/開發（非 PUBLIC）：用預設金鑰無妨（本機自己用，is_owner 恆真）。"""
    monkeypatch.setattr(auth.store, "PUBLIC", False)
    monkeypatch.setattr(auth, "SESSION_SECRET", "dev-insecure-change-me")
    assert auth._secret_ok()
    assert auth.is_owner(FakeRequest())


@pytest.mark.parametrize("secret", ["short-secret", "x" * 31, "  " + "y" * 31 + "  "])
def test_short_secret_rejected_on_public(monkeypatch, secret):
    """長度 <32 的金鑰可被暴力猜：公開站一律不採信 cookie（fail-closed，比照 ClinScope，審查 GA-13）。"""
    monkeypatch.setattr(auth, "SESSION_SECRET", secret)
    auth._SER = None
    try:
        assert not auth._secret_ok()
        req = FakeRequest(cookies={auth.COOKIE_NAME: auth.make_session(OWNER)})
        assert not auth.is_owner(req)
    finally:
        auth._SER = None


def test_32_char_secret_accepted(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", "z" * 32)
    assert auth._secret_ok()


def test_test_suite_secret_is_long_enough():
    """conftest 的測試金鑰也要 ≥32 字，否則 AUTH_ENABLED 關掉、整套 server 測試失去登入路徑。"""
    assert auth._secret_ok() and auth.AUTH_ENABLED
