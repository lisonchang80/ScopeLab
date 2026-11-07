"""公開站只列白名單範例專案（card.json `"public": true`，審查 SL-3）。

站主在公開站（本機）用原生選夾建的專案可能指向臨床／客戶影像：以前匿名訪客在下拉選它、逐張點，
就能從預覽與影像格拿到每一張原圖（Gradio 把它們複製進快取再服務，不經 allowed_paths）。
後端是權威：下拉清單、on_proj_change、bridge 選取、訓練／推論分頁都要擋，不能只靠前端下拉。
"""
import json

import pytest
from PIL import Image

import app
from conftest import DATA, OWNER, SLUG, TMP, FakeRequest, guest_cookies, owner_cookies
from src import auth, store
from src.infer import tab as infer_tab
from src.train import tab as train_tab

GUESTS = [pytest.param(None, id="anonymous"), pytest.param("guest", id="non-owner-login")]
SECRET = "secret_proj"
EXT = "ext_proj"


def _cookies(who):
    return owner_cookies() if who == "owner" else (guest_cookies() if who == "guest" else None)


def _req(who):
    return FakeRequest(cookies=_cookies(who) or {})


def _images(folder, prefix, n=2):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (16, 16), (199, 10, 10)).save(folder / f"{prefix}_{i}.png")
    return folder


def _card(slug, name, src, public=False):
    pd = DATA / "projects" / slug
    pd.mkdir(parents=True, exist_ok=True)
    card = {"slug": slug, "name": name, "discipline": "", "task": "cls", "classes": ["甲"],
            "author": "", "version": "0.1.0", "license": "", "description": "",
            "source_folder": str(src), "created": "2099-01-02", "updated": "2099-01-02"}
    if public:
        card["public"] = True
    (pd / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    (pd / "labels.json").write_text(json.dumps({"labels": {}, "edits": {}}), encoding="utf-8")


@pytest.fixture()
def secret():
    """站主私人專案：來源在 allowed_paths 內（DATA 底下），只靠白名單擋。"""
    _card(SECRET, "私人臨床專案", _images(DATA / "srcs" / "secret", "secretimg"))
    return SECRET


@pytest.fixture()
def ext():
    """標成公開、但來源資料夾在 allowed_paths 外的專案：縱深防禦要把檔篩掉。"""
    _card(EXT, "外部來源範例", _images(TMP / "ext_src", "extimg"), public=True)
    return EXT


def _slugs(choices):
    return [v for _, v in choices]


# ---- 下拉清單 -------------------------------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_lists_hide_private_projects_for_guest(secret, who):
    req = _req(who)
    assert _slugs(app.project_choices(req)) == [SLUG]
    assert _slugs(train_tab._all_projects(req)) == [SLUG]
    assert _slugs(infer_tab._all_projects(req)) == [SLUG]
    outs = app.on_page_projects(app.new_state(), None, None, req)
    for u in outs:
        assert _slugs(u["choices"]) == [SLUG]


def test_build_snapshot_is_public_only(secret):
    """build() 沒有 request：啟動快照（會寫進 /config）只放白名單。"""
    assert _slugs(app.project_choices()) == [SLUG]
    assert _slugs(train_tab._all_projects()) == [SLUG]


def test_owner_sees_all_projects(secret):
    req = _req("owner")
    assert sorted(_slugs(app.project_choices(req))) == sorted([SLUG, SECRET])
    assert sorted(_slugs(train_tab._all_projects(req))) == sorted([SLUG, SECRET])
    outs = app.on_page_projects(app.new_state(), None, None, req)
    assert sorted(_slugs(outs[0]["choices"])) == sorted([SLUG, SECRET])


def test_owner_preview_sees_only_public(secret):
    auth.set_local_mode(False)             # 站主取消本地模式＝預覽訪客
    assert _slugs(app.project_choices(_req("owner"))) == [SLUG]


@pytest.mark.parametrize("who", GUESTS)
def test_delete_dialog_lists_only_public_for_guest(secret, who):
    _, radio = app.on_del_open(app.new_state(), _req(who))
    assert _slugs(radio["choices"]) == [SLUG]


# ---- 選專案（直接打 API 指定 slug） -----------------------------------------------
# 直接呼叫 handler（等同繞過 UI、自己組 queue/join）：Gradio 的下拉檢查只看這個分頁收過的 choices，
# 站主分頁載入後會收到完整清單，不能當成權威——閘門要在 handler 裡。
def _call(fn, *args, request):
    import inspect
    if "request" in inspect.signature(fn).parameters:
        return fn(*args, request=request)
    return fn(*args)          # 修正前的 handler 沒有 request 參數：照舊呼叫，讓測試失敗在「洩漏」而不是 TypeError


def _sel(S, req, i=0):
    """回 bridge 送回瀏覽器的部分（第 0 個是伺服器端 State，不會送出去）。"""
    return _call(app.on_bridge, json.dumps({"a": "sel", "i": i}), S, request=req)[1:]


@pytest.mark.parametrize("who", GUESTS)
def test_proj_change_private_returns_blank_for_guest(secret, who):
    req = _req(who)
    out = _call(app.on_proj_change, SECRET, app.new_state(), request=req)
    assert "私人臨床專案" not in repr(out) and "secretimg" not in repr(out)
    S = out[0]
    assert S["files"] == [] and S["slug"] != SECRET
    # 同一個分頁接著點詳細表第 0 列：也拿不到原圖
    assert "secretimg" not in repr(_sel(S, req))


def test_proj_change_private_works_for_owner(secret):
    out = _call(app.on_proj_change, SECRET, app.new_state(), request=_req("owner"))
    assert "私人臨床專案" in repr(out) and "secretimg_0" in repr(out)
    assert "secretimg_0" in repr(_sel(out[0], _req("owner")))


def test_bridge_sel_blank_when_owner_switches_to_preview(secret):
    """站主開著私人專案就切到預覽訪客：bridge 選取回空白預覽。"""
    S = _call(app.on_proj_change, SECRET, app.new_state(), request=_req("owner"))[0]
    auth.set_local_mode(False)
    assert "secretimg" not in repr(_sel(S, _req("owner")))


def test_proj_change_private_rejected_via_api_for_guest(server, secret):
    """經 queue/join 打也一樣（這條在修正前也會因 Gradio 下拉檢查失敗，但要確定沒有洩漏）。"""
    ok, out = server.call("on_proj_change", [SECRET, None], session="secret-api")
    assert "私人臨床專案" not in json.dumps(out, ensure_ascii=False)
    assert "secretimg" not in json.dumps(out, ensure_ascii=False)


# ---- 縱深：公開專案的來源在 allowed_paths 外 ---------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_files_outside_allowed_paths_not_rendered_for_guest(ext, who):
    req = _req(who)
    out = _call(app.on_proj_change, EXT, app.new_state(), request=req)
    assert "extimg" not in repr(out)
    assert "extimg" not in repr(_sel(out[0], req))


def test_files_outside_allowed_paths_still_shown_to_owner(ext):
    out = _call(app.on_proj_change, EXT, app.new_state(), request=_req("owner"))
    assert "extimg_0" in repr(out)


def test_preview_bundle_skips_unservable_when_locked(ext):
    f = str(TMP / "ext_src" / "extimg_0.png")
    S = app.new_state(store.load_card(EXT))
    S["files"], S["selected"] = [f], f
    assert not app._servable(f)
    assert json.loads(app.preview_bundle(S, locked=True)[1]["value"]) == {}
    assert app.gallery_items(S, locked=True) == []
    assert json.loads(app.preview_bundle(S, locked=False)[1]["value"])["path"] == f   # 站主照常


# ---- 訓練／推論分頁 --------------------------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_infer_tab_hides_private_runs_for_guest(server, secret, who):
    run = DATA / "projects" / SECRET / "runs" / "20990101-000000"
    run.mkdir(parents=True, exist_ok=True)
    (run / "best.pt").write_bytes(b"")
    (run / "model_card.json").write_text(json.dumps({"name": "私人模型", "task": "cls"}), encoding="utf-8")
    fns = {bf.fn.__qualname__: bf.fn for bf in server.blocks.fns.values() if bf.fn is not None}
    on_proj = fns["build_infer_tab.<locals>.on_proj_change"]
    on_run = fns["build_infer_tab.<locals>.on_run_change"]
    runs_upd, card_md = on_proj(SECRET, _req(who))
    assert runs_upd["choices"] == [] and card_md == ""
    assert on_run(str(run / "best.pt"), _req(who)) == ""
    # 站主照常看得到
    runs_upd, card_md = on_proj(SECRET, _req("owner"))
    assert runs_upd["choices"] and "私人模型" in card_md
    assert "私人模型" in on_run(str(run / "best.pt"), _req("owner"))
