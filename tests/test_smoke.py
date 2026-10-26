"""冒煙：公開站模式起得來、首頁 200、唯讀事件照常可用（訪客 / 站主皆可）。"""
from conftest import owner_cookies


def test_home_page_ok(server):
    with server.client() as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "ScopeLab" in r.text


def test_readonly_event_works_for_guest_and_owner(server):
    for cookies in (None, owner_cookies()):
        ok, out = server.call("on_view", ["👁 圖示", None], cookies=cookies)
        assert ok, out
