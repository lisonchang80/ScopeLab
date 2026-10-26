"""bridge「editor_save」的 path 由前端送來：只接受目前專案已載入的影像，
其他路徑（專案外的檔案）一律忽略——不預覽、不寫進 labels.json。"""
import json

import pytest
from PIL import Image

from conftest import IMAGES, TMP, owner_cookies, read_labels


@pytest.fixture()
def outside_png():
    d = TMP / "outside"
    d.mkdir(exist_ok=True)
    p = d / "not_in_project.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(p)
    return p


def _editor_save(server, path, cookies, session):
    obj = {"a": "editor_save", "path": str(path), "img": None,
           "boxes": [{"cls": "甲", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}]}
    return server.call("on_bridge", [json.dumps(obj, ensure_ascii=False), None],
                       cookies=cookies, session=session)


def _preview_path(out):
    data_box = out["data"][6]                     # BRIDGE_OUT[6] = data_box（給編輯器的 JSON）
    value = data_box.get("value") if isinstance(data_box, dict) else data_box
    return json.loads(value or "{}").get("path")


@pytest.mark.parametrize("cookies", [None, "owner"], ids=["anonymous", "owner"])
def test_editor_save_ignores_path_outside_project(server, outside_png, cookies):
    ck = owner_cookies() if cookies == "owner" else None
    ok, out = _editor_save(server, outside_png, ck, session=f"outside-{cookies}")
    assert ok, out
    assert _preview_path(out) != str(outside_png)
    assert str(outside_png) not in read_labels()["labels"]
    assert str(outside_png) not in read_labels()["edits"]


def test_editor_save_accepts_project_image(server):
    ok, out = _editor_save(server, IMAGES / "img_2.png", owner_cookies(), session="inside")
    assert ok, out
    assert _preview_path(out) == str(IMAGES / "img_2.png")
