"""drive.py の終わりの結果 JSON が見送った指摘を 1 件ずつ持つ（#1488 の「レビュー」の材料の供給側）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load():
    """同じ名前で 1 度だけ読む（耐久ワークフローの登録を 1 つのモジュールに保つ）。"""
    if "cross_review_drive" in sys.modules:
        return sys.modules["cross_review_drive"]
    spec = importlib.util.spec_from_file_location("cross_review_drive", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cross_review_drive"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def _drive(tmp_path: Path, state: dict, sweep: dict | None = None):
    d = object.__new__(cr.Drive)
    d.pr, d.v, d.env = 5, {"TMP_DIR": str(tmp_path), "WORKTREE": ""}, {}
    (tmp_path / "cross-review-pr5-state.json").write_text(json.dumps(state), encoding="utf-8")
    if sweep is not None:
        d.path("sweep").write_text(json.dumps(sweep), encoding="utf-8")
    return d


STATE = {
    "rounds": [{"fix": {"fixed": 1, "deferred": 2}}, {"fix": {"deferred": 0}}],
    "deferred_nits": [
        {"path": "a/b.py", "line": 191, "summary": "照合に本文を足す", "round": 1, "severity": "nit"},
        {"path": "c.md", "line": None, "summary": "言い換え", "round": 1},
        {"path": "d.py", "line": 3, "summary": "基準外", "round": 1, "waived": True},
    ],
}


def test_finish_adds_each_deferred_finding(monkeypatch, tmp_path):
    """1488-1（供給側）: 修正の見送りと最終スイープの見送りが path・line つきで items に出て、report と metrics は変わらない。"""
    d = _drive(tmp_path, STATE, {"deferred": [{"summary": "本文の指摘", "reason_for_deferral": "要ユーザ判断"}, "文字列"]})
    monkeypatch.setattr(cr, "call", lambda *a, **k: (0, ""))
    monkeypatch.setattr(d, "st", lambda *a: (0, "report"), raising=False)
    res = d.finish()
    assert set(res["items"][0]) == {"report"} and res["metrics"]["rounds"] == 2 and res["metrics"]["deferred"] == 2
    got = [(i["path"], i["line"], i["summary"], i["round"]) for i in res["items"][1:]]
    assert got == [("a/b.py", 191, "照合に本文を足す", 1), ("c.md", None, "言い換え", 1), (None, None, "本文の指摘", 2)]
    assert all(i["kind"] == "deferred" for i in res["items"][1:])


def test_no_deferred_findings_add_nothing(monkeypatch, tmp_path):
    d = _drive(tmp_path, {"rounds": []}, {"deferred": []})
    monkeypatch.setattr(cr, "call", lambda *a, **k: (0, ""))
    monkeypatch.setattr(d, "st", lambda *a: (0, "report"), raising=False)
    assert [set(i) for i in d.finish()["items"]] == [{"report"}]
