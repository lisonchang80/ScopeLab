"""登出必須在伺服器端讓該 session 失效，不只是請瀏覽器刪 cookie（審查 GA-9，比照 ClinScope tests/test_logout.py）。

以前 make_session 只簽 {"email": ...}、/auth/logout 只 delete_cookie：被複製走的站主 cookie 值登出後
30 天內照樣有效。現在 cookie 帶隨機 sid，登出把站主的 sid 記進持久化作廢檔（DATA_ROOT/session_revoked.json）。
"""
import json
import time
import uuid

import pytest
from fastapi import FastAPI
from itsdangerous import URLSafeTimedSerializer
from starlette.testclient import TestClient

from conftest import OWNER, TMP, FakeRequest
from src import auth as A

GUEST = "visitor@example.org"
_NB_KEY = "__not_before__"
_ORIG_FILE = getattr(A, "_REVOKED_FILE", None)      # autouse fixture 換掉之前的正式位置


@pytest.fixture(autouse=True)
def isolated_revoked(monkeypatch):
    """每條測試自己的作廢檔與記憶體狀態；結束後還原（不讓 not_before 漏到其他測試）。"""
    monkeypatch.setattr(A, "_REVOKED_FILE", TMP / f"revoked_{uuid.uuid4().hex[:8]}.json", raising=False)
    monkeypatch.setattr(A, "_REVOKED", {}, raising=False)
    monkeypatch.setattr(A, "_NOT_BEFORE", 0.0, raising=False)
    return monkeypatch


def _client():
    app = FastAPI()
    A.add_auth_routes(app)
    return TestClient(app)


def _logout(tok):
    return _client().post("/auth/logout", json={}, headers={"Cookie": f"{A.COOKIE_NAME}={tok}"})


def _owner(tok):
    return A.is_owner(FakeRequest(cookies={A.COOKIE_NAME: tok}))


def _restart(mp):
    """新行程：模組全域重新初始化（_NOT_BEFORE 回到 0、_REVOKED 只靠檔案還原）。"""
    mp.setattr(A, "_NOT_BEFORE", 0.0, raising=False)
    A._REVOKED.clear()
    A._REVOKED.update(A._load_revoked())


def _file_json():
    return json.loads(A._REVOKED_FILE.read_text(encoding="utf-8"))


def test_revoked_file_lives_under_data_root():
    assert _ORIG_FILE == A.store.DATA_ROOT / "session_revoked.json"     # .gitignore 有列


def test_logout_revokes_session_server_side():
    tok = A.make_session(OWNER)
    assert _owner(tok)
    r = _logout(tok)
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert A.COOKIE_NAME.lower() in sc and "max-age=0" in sc and "httponly" in sc
    assert not _owner(tok)
    assert A.read_session(tok) is None


def test_logout_only_revokes_that_session():
    a, b = A.make_session(OWNER), A.make_session(OWNER)
    _logout(a)
    assert not _owner(a)
    assert _owner(b)


def test_revocation_survives_server_restart(isolated_revoked):
    tok = A.make_session(OWNER)
    _logout(tok)
    _restart(isolated_revoked)
    assert not _owner(tok)


def test_legacy_cookie_without_session_id_rejected():
    legacy = URLSafeTimedSerializer(A.SESSION_SECRET, salt="scopelab-session").dumps({"email": OWNER})
    assert not _owner(legacy)


def test_guest_logout_is_not_recorded():
    _logout(A.make_session(GUEST))
    assert A._REVOKED == {}
    assert not A._REVOKED_FILE.exists()


def test_revoked_list_is_capped(isolated_revoked):
    isolated_revoked.setattr(A, "_REVOKED_MAX", 3, raising=False)
    toks = [A.make_session(OWNER) for _ in range(5)]
    for t in toks:
        _logout(t)
    assert len(A._REVOKED) <= 3
    assert not _owner(toks[-1])
    assert len(_file_json()) <= 3


def test_revoked_file_is_written_atomically(monkeypatch):
    import os
    seen = []
    real = A.os.replace

    def _spy(src, dst):
        seen.append((str(src), str(dst)))
        return real(src, dst)

    monkeypatch.setattr(A.os, "replace", _spy)
    _logout(A.make_session(OWNER))
    assert seen and seen[-1][1] == str(A._REVOKED_FILE)
    assert os.path.dirname(seen[-1][0]) == os.path.dirname(str(A._REVOKED_FILE))


def test_truncated_revoked_file_fails_closed(isolated_revoked):
    tok = A.make_session(OWNER)
    _logout(tok)
    raw = A._REVOKED_FILE.read_bytes()
    A._REVOKED_FILE.write_bytes(raw[: len(raw) // 2])        # 寫到一半被殺（重開機／taskkill）
    _restart(isolated_revoked)
    assert not _owner(tok), "壞檔不可讓已登出（可能外流）的 cookie 復活"
    assert list(A._REVOKED_FILE.parent.glob(A._REVOKED_FILE.name + ".corrupt*")), "壞檔要改名保留"
    time.sleep(1.1)                                          # itsdangerous 時戳以秒計
    assert _owner(A.make_session(OWNER)), "重新登入的 cookie 要能用"


def test_missing_revoked_file_is_a_normal_first_start(isolated_revoked):
    isolated_revoked.setattr(A, "_REVOKED_FILE", A._REVOKED_FILE.with_name("never_written.json"))
    assert A._load_revoked() == {}
    assert A._NOT_BEFORE == 0.0
    assert _owner(A.make_session(OWNER))


def test_corrupt_file_fail_closed_survives_second_restart(isolated_revoked):
    tok = A.make_session(OWNER)
    _logout(tok)
    raw = A._REVOKED_FILE.read_bytes()
    A._REVOKED_FILE.write_bytes(raw[: len(raw) // 2])
    _restart(isolated_revoked)
    assert not _owner(tok)
    assert _NB_KEY in _file_json(), "not_before 要寫進作廢檔本身，不能只放記憶體"
    _restart(isolated_revoked)
    assert not _owner(tok), "第二次重啟後已作廢的站主 cookie 不可復活"
    _restart(isolated_revoked)
    assert not _owner(tok)


def test_not_before_key_survives_logouts_and_is_not_a_sid_or_counted(isolated_revoked):
    nb = time.time() - 5
    A._REVOKED_FILE.write_text(json.dumps({_NB_KEY: nb}), encoding="utf-8")
    _restart(isolated_revoked)
    assert A._NOT_BEFORE == nb and _NB_KEY not in A._REVOKED
    isolated_revoked.setattr(A, "_REVOKED_MAX", 2, raising=False)
    toks = [A.make_session(OWNER) for _ in range(4)]
    for t in toks:
        _logout(t)
    d = _file_json()
    assert d.get(_NB_KEY) == nb, "登出寫檔、剔除舊項目都要保留 not_before"
    assert len([k for k in d if k != _NB_KEY]) == 2 and len(A._REVOKED) == 2, "not_before 不算進上限"
    _restart(isolated_revoked)
    assert A._NOT_BEFORE == nb and _NB_KEY not in A._REVOKED


def test_not_before_key_dropped_after_max_age(isolated_revoked):
    old = time.time() - A.SESSION_MAX_AGE - 60
    live_sid_exp = time.time() + 3600
    A._REVOKED_FILE.write_text(json.dumps({_NB_KEY: old, "somesid": live_sid_exp}), encoding="utf-8")
    _restart(isolated_revoked)
    assert A._NOT_BEFORE == 0.0, "過了 SESSION_MAX_AGE，之前簽發的 cookie 早已自然過期 → 不必再擋"
    assert A._REVOKED == {"somesid": live_sid_exp}
    assert _owner(A.make_session(OWNER))
    _logout(A.make_session(OWNER))
    assert _NB_KEY not in _file_json()


def test_corrupt_file_put_back_when_not_before_cannot_be_written(isolated_revoked):
    tok = A.make_session(OWNER)
    _logout(tok)
    raw = A._REVOKED_FILE.read_bytes()
    A._REVOKED_FILE.write_bytes(raw[: len(raw) // 2])
    real_write = A._write_revoked
    isolated_revoked.setattr(A, "_write_revoked", lambda *a, **k: False)   # 磁碟滿：新檔寫不進去
    _restart(isolated_revoked)
    assert not _owner(tok)
    assert A._REVOKED_FILE.exists(), "寫不進新檔時要把壞檔搬回原處"
    isolated_revoked.setattr(A, "_write_revoked", real_write)
    _restart(isolated_revoked)
    assert not _owner(tok), "下次重啟仍要 fail-closed，舊 cookie 不可復活"


def test_logout_via_real_server_revokes(server):
    """走正式站同一條 launch_app 路徑：登出後舊 cookie 打站主閘門變回訪客。"""
    tok = A.make_session(OWNER)
    with server.client(cookies={A.COOKIE_NAME: tok}) as c:
        assert c.post("/auth/logout").status_code == 200
    assert not _owner(tok)
