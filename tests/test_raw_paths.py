"""檔案閘門要看「Gradio 路由實際比對的同一個字串」（scope["path"]），不能看 request.url.path（審查 SL-3 第二次退回）。

Starlette 的 request.url 是用已解碼的 path 重組網址再拆一次：路徑裡的 %3F／%23 解碼成 ?／# 之後，後面整段被當成
query／fragment 切掉，閘門只看到 `…/zz`；Gradio 拿到完整的 `…/zz?/../projects/privproj/card.json`，abspath 消掉
`zz?/..` 照樣讀檔。httpx 會在用戶端把 `..` 正規化，所以這裡用 http.client 送原始請求。
"""
import http.client
import json
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest
from PIL import Image

import app
from conftest import DATA, owner_cookies
from src import auth

TRICKS = ["%3F", "%23", "%3f"]


def _raw(server, path, cookies=None):
    u = urlsplit(server.base)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    try:
        c.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", u.netloc)
        if cookies:
            c.putheader("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
        c.endheaders()
        r = c.getresponse()
        body = r.read()
        return r.status, body
    finally:
        c.close()


@pytest.fixture()
def targets():
    """私人專案 card.json、私人來源原圖、公開專案的 runs 權重、exports 匯出檔（都在 allowed 的 DATA 內）。"""
    src = DATA / "srcs" / "rawpriv"
    src.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (199, 10, 10)).save(src / "rawimg_0.png")
    pd = DATA / "projects" / "rawpriv"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "card.json").write_text(json.dumps({"slug": "rawpriv", "name": "RAWPRIVNAME", "task": "cls",
                                              "source_folder": str(src)}), encoding="utf-8")
    run = DATA / "projects" / "demo" / "runs" / "r1"
    run.mkdir(parents=True, exist_ok=True)
    (run / "best.pt").write_bytes(b"WEIGHTS")
    (DATA / "exports").mkdir(exist_ok=True)
    (DATA / "exports" / "e.csv").write_text("EXPORT", encoding="utf-8")
    return {"card": "projects/rawpriv/card.json", "source": "srcs/rawpriv/rawimg_0.png",
            "weights": "projects/demo/runs/r1/best.pt", "exports": "exports/e.csv"}


def _url(rel, trick=None, prefix="/gradio_api/file="):
    d = DATA.as_posix()
    return prefix + (f"{d}/zz{trick}/../{rel}" if trick else f"{d}/{rel}")


@pytest.mark.parametrize("trick", TRICKS)
@pytest.mark.parametrize("kind", ["card", "source", "weights", "exports"])
def test_query_fragment_trick_blocked_for_anonymous(server, targets, trick, kind):
    status, body = _raw(server, _url(targets[kind], trick))
    assert status == 403, (kind, trick, status, body[:80])


@pytest.mark.parametrize("kind", ["card", "source", "weights", "exports"])
def test_plain_private_paths_blocked_for_anonymous(server, targets, kind):
    assert _raw(server, _url(targets[kind]))[0] == 403


@pytest.mark.parametrize("trick", TRICKS)
@pytest.mark.parametrize("kind", ["card", "weights", "exports"])
def test_query_fragment_trick_owner_still_served(server, targets, trick, kind):
    assert _raw(server, _url(targets[kind], trick), owner_cookies())[0] == 200


@pytest.mark.parametrize("trick", [None] + TRICKS)
def test_public_example_served_to_anonymous(server, targets, trick):
    note = DATA / "projects" / "demo" / "edited" / "demo_note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("demo", encoding="utf-8")
    status, body = _raw(server, _url("projects/demo/edited/demo_note.txt", trick))
    assert status == 200 and b"demo" in body


@pytest.mark.parametrize("trick", [None] + TRICKS)
def test_public_example_card_owner_only(server, targets, trick):
    """公開範例的 card.json 也只給站主（含本機路徑，審查 GA-4）。"""
    assert _raw(server, _url("projects/demo/card.json", trick))[0] == 403
    assert _raw(server, _url("projects/demo/card.json", trick), owner_cookies())[0] == 200


def test_old_route_with_trick_blocked(server, targets):
    assert _raw(server, _url(targets["card"], "%3F", prefix="/gradio_api/file/"))[0] == 403


@pytest.mark.parametrize("trick", TRICKS)
def test_owner_only_post_trick_does_not_reach_upload(server, trick):
    """擋單用 scope["path"]：/gradio_api/upload%3F… 在 Gradio 那邊也對不到 upload 路由（不是 403 就得是 404／405）。"""
    u = urlsplit(server.base)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    try:
        c.request("POST", f"/gradio_api/upload{trick}x", body=b"", headers={"Content-Length": "0"})
        assert c.getresponse().status in (403, 404, 405)
    finally:
        c.close()


def test_guard_paths_use_scope_path():
    assert auth.owner_only_post("POST", "/gradio_api/upload\n")       # Starlette 的 $ 也吃結尾換行：兩邊一致
    assert app._private_file(str(DATA / "exports") + "::$INDEX_ALLOCATION/e.csv")   # NTFS 串流語法：一律當私有


# ---- projects/ 底下的 junction 指到別處（審查 SL-3 第二次退回，資訊） ---------------------
@pytest.fixture()
def junction():
    target = DATA / "elsewhere" / "juncproj"
    target.mkdir(parents=True, exist_ok=True)
    (target / "card.json").write_text(json.dumps({"slug": "junc", "name": "JUNCNAME", "task": "cls"}),
                                      encoding="utf-8")
    link = DATA / "projects" / "junc"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("建不了 junction：" + r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace"))
    try:
        yield link
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)   # 只拆連結，不碰目標
        shutil.rmtree(DATA / "elsewhere", ignore_errors=True)


def test_junction_under_projects_is_private(server, junction):
    assert app._private_file(str(junction / "card.json"))
    status, body = _raw(server, _url("projects/junc/card.json"))
    assert status == 403 and b"JUNCNAME" not in body


def test_junction_owner_still_served(server, junction):
    assert _raw(server, _url("projects/junc/card.json"), owner_cookies())[0] == 200
