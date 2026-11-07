"""身分紀錄（identity_debug.log）只記登入者、有 1MB 上限（審查 GA-6，比照 ClinScope）。

identity_bar 掛在 demo.load，匿名也能經 /gradio_api/queue/join 反覆觸發：以前每次都追加一行，
任何人狂打就能無限長檔吃滿 D:。
"""
import pytest

from conftest import OWNER, FakeRequest, guest_cookies, owner_cookies
from src import auth


@pytest.fixture()
def idlog(tmp_path, monkeypatch):
    p = tmp_path / "identity_debug.log"
    monkeypatch.setattr(auth, "_ID_LOG", p)
    return p


def _lines(p):
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_anonymous_load_writes_nothing(idlog):
    for _ in range(5):
        auth.identity_bar(FakeRequest())
    assert _lines(idlog) == []


def test_anonymous_load_via_api_writes_nothing(server, idlog):
    for _ in range(3):
        ok, out = server.call("identity_bar", [])
        assert ok, out
    assert _lines(idlog) == []


@pytest.mark.parametrize("cookies,email", [(owner_cookies, OWNER), (guest_cookies, "visitor@example.org")])
def test_logged_in_load_is_logged(idlog, cookies, email):
    auth.identity_bar(FakeRequest(cookies=cookies()))
    (line,) = _lines(idlog)
    assert f"email={email}" in line


def test_log_stops_at_size_cap(idlog, monkeypatch):
    monkeypatch.setattr(auth, "_ID_LOG_MAX_BYTES", 64)
    idlog.write_text("x" * 64, encoding="utf-8")
    auth.identity_bar(FakeRequest(cookies=owner_cookies()))
    assert idlog.read_text(encoding="utf-8") == "x" * 64
