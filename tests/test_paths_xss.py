"""路徑穿越的縱深防禦 + 既有 HTML 轉義的回歸鎖。"""
import pytest

import app
from conftest import SLUG
from src import store


# ---- slug 不得逸出 projects/（project_dir / delete_project 的縱深防禦） -----------
@pytest.mark.parametrize("bad", ["../evil", "..\\evil", "a/b", "a\\b", "C:/x", "..", "/etc/passwd"])
def test_project_dir_rejects_traversal(bad):
    with pytest.raises(ValueError):
        store.project_dir(bad)


def test_delete_project_rejects_traversal(tmp_path):
    victim = store.PROJECTS.parent / "victim.txt"
    victim.write_text("keep")
    try:
        with pytest.raises(ValueError):
            store.delete_project("../victim.txt")
        assert victim.exists()               # 沒被刪到 projects/ 外
    finally:
        victim.unlink(missing_ok=True)


def test_normal_and_cjk_slug_ok():
    assert store.project_dir("shapes_demo").name == "shapes_demo"
    assert store.project_dir("形狀示範").name == "形狀示範"


def test_slugify_collapses_separators():
    for name in ["../../etc", "a/b\\c", "C:\\x"]:
        s = store.slugify(name)
        assert "/" not in s and "\\" not in s and ".." not in s.split("_")


# ---- 詳細表 HTML：類別 / 檔名一律轉義（既有行為鎖） ------------------------------
def test_detail_html_escapes_class_and_name(tmp_path):
    payload = '<img src=x onerror=alert(1)>'
    img = tmp_path / f"{payload}.png"      # 檔名帶惡意（folder scan 來源）
    # 不實際建檔：Windows 檔名不能有 < >（正式站就在 Windows），detail_html_str 也只用到路徑字串
    S = app.new_state({"slug": "", "name": "p", "task": "cls", "classes": [payload]})
    S["files"] = [str(img)]
    S["labels"] = {str(img): payload}
    S["page_files"] = S["files"]
    out = app.detail_html_str(S)
    assert "<img src=x onerror=" not in out
    assert "&lt;img" in out                # 轉義後的形式
