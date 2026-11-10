"""読むだけの取り出し（#2909 の F1・F2 と `step_result.py pick`）。

スプリントの状態・開発ループの記録・結果 JSON から、次の操作と JSON Pointer の値を返す。状態ファイルを書き換えない（I2）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import json_pick  # noqa: E402
import step_result  # noqa: E402


def run(*args: str, stdin: str | None = None) -> tuple[int, dict | str]:
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=60, input=stdin, env=dict(os.environ))
    try:
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return p.returncode, p.stdout


def sprint_state(tmp_path: Path) -> Path:
    m = {
        "name": "s1",
        "plans": [
            {"kind": "実装", "plan": "a.json", "label": "実装 #1", "next": "開発版へ", "row": {"result": "完了", "pr": "#10"}},
            {"kind": "開発版", "plan": "b.json", "label": "開発版 1.0.1", "next": "本番へ", "row": {"result": "まだ", "pr": ""}},
            {"kind": "本番", "plan": "c/d.json", "label": "本番 1.0.1", "next": ""},
        ],
        "gates": [],
        "done": [],
    }
    p = tmp_path / "sprint.json"
    p.write_text(json.dumps(m, ensure_ascii=False))
    return p


def unchanged(path: Path):
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    return lambda: (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_sprint_get_returns_rows_and_the_first_open_plan_as_next(tmp_path):
    p = sprint_state(tmp_path)
    same = unchanged(p)
    code, out = run(str(SCRIPTS / "sprint-state.py"), "get", str(p))
    assert code == 0 and step_result.validate_result(out, code) == []
    m = json.loads(p.read_text())
    # 今 `python -c` で読む値（plans[i].next・row.result）と同じ値が返る
    assert [i["next"] for i in out["items"]] == [x["next"] for x in m["plans"]]
    assert [i["result"] for i in out["items"]] == ["完了", "まだ", "まだ"]
    assert out["metrics"] == {"plans": 3, "done": 1, "open": 2}
    assert out["next"] == "開発版 1.0.1: 本番へ"
    assert same()


def test_sprint_get_field_returns_values_and_marks_missing(tmp_path):
    p = sprint_state(tmp_path)
    code, out = run(str(SCRIPTS / "sprint-state.py"), "get", str(p), "--field", "/plans/0/row/pr", "--field", "/plans/9/next")
    assert code == 0 and step_result.validate_result(out, code) == []
    assert out["metrics"] == {"/plans/0/row/pr": "#10", "/plans/9/next": None}
    assert out["items"] == [{"kind": "field", "name": "/plans/9/next", "result": "missing"}]


@pytest.mark.parametrize(
    "args,want",
    [((), 3), (("--field", "plans"), 2)],
)
def test_sprint_get_failures(tmp_path, args, want):
    target = tmp_path / "none.json" if not args else sprint_state(tmp_path)
    code, out = run(str(SCRIPTS / "sprint-state.py"), "get", str(target), *args)
    assert code == want and out["status"] == "stopped"


def loop_root(tmp_path: Path, rec: dict) -> Path:
    d = tmp_path / ".ndf" / "loop" / "l1"
    d.mkdir(parents=True)
    (d / "loop.json").write_text(json.dumps({"name": "l1", "status": "running", "budget": {}, "laps": [], "goal": True, **rec}))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


@pytest.mark.parametrize(
    "rec,want",
    [
        ({"stop": {"condition": "budget", "values": {}}, "waiting": [{"number": 5}]}, "止まった理由: budget"),
        ({"waiting": [{"number": 5}, {"number": 6, "answer": {"result": "approved"}}]}, "人の判断待ち: #5。"),
        ({}, None),
    ],
)
def test_loop_get_next_follows_the_table(tmp_path, rec, want):
    root = loop_root(tmp_path, rec)
    path = root / ".ndf" / "loop" / "l1" / "loop.json"
    same = unchanged(path)
    code, out = run(str(SCRIPTS / "sprint-state.py"), "loop", "get", "l1", "--root", str(root))
    assert code == 0 and step_result.validate_result(out, code) == []
    if want is None:
        assert "next" not in out
    else:
        assert out["next"].startswith(want)
    _, status = run(str(SCRIPTS / "sprint-state.py"), "loop", "status", "l1", "--root", str(root), "--json")
    assert out["metrics"] == status["metrics"]
    assert same()


def test_loop_get_field_and_missing_record(tmp_path):
    root = loop_root(tmp_path, {"waiting": [{"number": 5}]})
    code, out = run(str(SCRIPTS / "sprint-state.py"), "loop", "get", "l1", "--root", str(root), "--field", "/waiting/0/number")
    assert code == 0 and out["metrics"] == {"/waiting/0/number": 5}
    code, out = run(str(SCRIPTS / "sprint-state.py"), "loop", "get", "l2", "--root", str(root))
    assert code == 3 and out["status"] == "stopped"


def test_json_pointer_escapes_and_indexes():
    doc = {"a/b": {"m~n": [1, 2]}, "": 0}
    assert json_pick.resolve_pointer(doc, "/a~1b/m~0n/1") == (2, True)
    assert json_pick.resolve_pointer(doc, "/a~1b/m~0n/01") == (None, False)
    assert json_pick.resolve_pointer(doc, "/") == (0, True)
    assert json_pick.resolve_pointer(doc, "") == (doc, True)
    for bad in ("a", "/x~2"):
        with pytest.raises(json_pick.PointerError):
            json_pick.parse_pointer(bad)


def emitted(**kw) -> str:
    return json.dumps(step_result.result("handoff", **kw), ensure_ascii=False)


def test_pick_copies_status_and_next_and_picks_values():
    src = emitted(status="gate", summary="s", items=[{"path": "/x/y"}], metrics={"n": 2}, next="答えを待つ")
    code, out = run(
        str(SCRIPTS / "lib" / "step_result.py"), "pick", "--field", "/items/0/path", "--field", "/metrics/n", stdin="noise\n" + src + "\n"
    )
    assert code == 10 and step_result.validate_result(out, code) == []
    assert out["status"] == "gate" and out["next"] == "答えを待つ"
    assert out["metrics"] == {"/items/0/path": "/x/y", "/metrics/n": 2}


def test_pick_raw_prints_the_value_only():
    src = emitted(status="ok", summary="s", items=[{"path": "/x/y"}])
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "lib" / "step_result.py"), "pick", "--field", "/items/0/path", "--raw"],
        input=src,
        capture_output=True,
        text=True,
    )
    assert (p.returncode, p.stdout) == (0, "/x/y\n")
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "lib" / "step_result.py"), "pick", "--field", "/items/5/path", "--raw"],
        input=src,
        capture_output=True,
        text=True,
    )
    assert (p.returncode, p.stdout) == (3, "\n")


def test_pick_rejects_input_that_is_not_a_result():
    code, out = run(str(SCRIPTS / "lib" / "step_result.py"), "pick", "--field", "/a", stdin='{"a": 1}\n')
    assert code == 2 and out["status"] == "stopped"


def glossary_root(tmp_path: Path) -> Path:
    (tmp_path / ".ndf").mkdir()
    decl = {"version": 1, "format": "json", "source": "g.json", "document": "g.md"}
    (tmp_path / ".ndf" / "glossary.json").write_text(json.dumps(decl))
    g = {
        "version": 1,
        "contexts": [{"id": "c1", "name": "一", "meaning": "m"}, {"id": "c2", "name": "二", "meaning": "m"}],
        "terms": [
            {"term": "ターンの型", "context": "c1", "meaning": "分類", "source": "a.md"},
            {"term": "機械的なターン", "context": "c1", "meaning": "呼び出し"},
            {"term": "結果 JSON", "context": "c2", "meaning": "1 行の JSON"},
        ],
    }
    (tmp_path / "g.json").write_text(json.dumps(g, ensure_ascii=False))
    return tmp_path


def test_glossary_lookup_matches_partially_and_filters_by_context(tmp_path):
    root = glossary_root(tmp_path)
    src = root / "g.json"
    same = unchanged(src)
    glossary = str(SCRIPTS / "glossary.py")
    code, out = run(glossary, "lookup", "--term", "ターン", "--root", str(root))
    assert code == 0 and step_result.validate_result(out, code) == []
    assert [(i["name"], i["result"]) for i in out["items"]] == [("ターンの型", "c1"), ("機械的なターン", "c1")]
    assert out["items"][0]["meaning"] == "分類" and out["metrics"] == {"matched": 2}
    code, out = run(glossary, "lookup", "--context", "c2", "--root", str(root))
    assert [i["name"] for i in out["items"]] == ["結果 JSON"]
    code, out = run(glossary, "lookup", "--contexts", "--root", str(root), "--field", "/metrics/contexts")
    assert code == 0 and out["metrics"] == {"/metrics/contexts": ["c1", "c2"]} and out["items"] == []
    assert same()


def test_glossary_field_keeps_status_and_exit_of_check(tmp_path):
    root = glossary_root(tmp_path)
    glossary = str(SCRIPTS / "glossary.py")
    code_plain, plain = run(glossary, "check", "--rules", "structure", "--root", str(root))
    code, out = run(glossary, "check", "--rules", "structure", "--root", str(root), "--field", "/metrics/hits")
    assert (code, out["status"]) == (code_plain, plain["status"])
    assert out["metrics"] == {"/metrics/hits": plain["metrics"]["hits"]} and out["items"] == []
    code, out = run(glossary, "check", "--root", str(root), "--field", "metrics")
    assert code == 2


def test_glossary_lookup_without_declaration_is_precondition(tmp_path):
    code, out = run(str(SCRIPTS / "glossary.py"), "lookup", "--root", str(tmp_path))
    assert code == 3 and out["status"] == "stopped"
