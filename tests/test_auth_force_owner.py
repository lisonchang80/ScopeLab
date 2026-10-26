"""SCOPELAB_FORCE_OWNER 是本機/桌面的放行捷徑；公開站（PUBLIC=1）必須忽略它，
否則誤設環境變數就讓全站任何人變站主（owner-mode skill 明載正式站絕不設）。"""
import pytest

from conftest import FakeRequest
from src import auth


def test_force_owner_ignored_on_public(monkeypatch):
    monkeypatch.setenv("SCOPELAB_FORCE_OWNER", "1")
    monkeypatch.setattr(auth.store, "PUBLIC", True)
    assert not auth.is_owner(FakeRequest())           # 匿名仍是訪客
    assert auth.public_locked(FakeRequest())


def test_force_owner_works_offline(monkeypatch):
    monkeypatch.setenv("SCOPELAB_FORCE_OWNER", "1")
    monkeypatch.setattr(auth.store, "PUBLIC", False)
    assert auth.is_owner(FakeRequest())


def test_empty_owner_email_never_matches(monkeypatch):
    """OWNER_EMAIL 若誤設為空：沒有任何人是站主（空 == 空 的巧合不該放行）。"""
    monkeypatch.setattr(auth.store, "PUBLIC", True)
    monkeypatch.setattr(auth, "OWNER_EMAIL", "")
    # 連「email 為空的 cookie」也不該被當成站主
    req = FakeRequest(cookies={auth.COOKIE_NAME: auth.make_session("")})
    assert not auth.is_owner(req)
