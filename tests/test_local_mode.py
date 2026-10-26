"""「本地模式」是程序層級的全域旗標：只有站主能改；訪客呼叫切換事件不得把站主鎖掉。"""
from conftest import guest_cookies, owner_cookies
from src import auth

TOGGLE = "build.<locals>._toggle_local"


def test_guest_cannot_turn_off_local_mode(server):
    auth.set_local_mode(True)
    for cookies in (None, guest_cookies()):
        server.call(TOGGLE, [False], cookies=cookies)
        assert auth.local_mode_on()


def test_owner_can_toggle_local_mode(server):
    ok, _ = server.call(TOGGLE, [False], cookies=owner_cookies())
    assert ok and not auth.local_mode_on()
    ok, _ = server.call(TOGGLE, [True], cookies=owner_cookies())
    assert ok and auth.local_mode_on()
