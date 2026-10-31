"""承認ゲートの差し戻しの記録と件数（`gate-rejections.py`・`lib/gate_rejections.py`、#1833）。

記録の置き場（`NDF_MVV_STATE_DIR`）は conftest が tmp へ向ける。gh も LLM も呼ばない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "gate-rejections.py"
STATE_PY = SCRIPTS / "sprint-state.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
import gate_rejections as gr  # noqa: E402
import project_mvv_signals as pms  # noqa: E402
from step_result import validate_result  # noqa: E402

AT = "2026-10-10T00:00:00+00:00"


def run(*args: str, cwd: Path | None = None, script: Path = SCRIPT) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=30, cwd=cwd)
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    if out:
        assert validate_result(out) == [], (out, p.stderr)
    return p.returncode, out


def ledger() -> Path:
    return Path(os.environ["NDF_MVV_STATE_DIR"]) / gr.LEDGER_NAME


def rows() -> list[dict]:
    return gr.read_rejections()


def record(*args: str, cwd: Path | None = None) -> tuple[int, dict]:
    return run("record", "--repo", "o/r", "--at", AT, *args, cwd=cwd)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_record_appends_one_row_with_gate_time_summary_repo_and_scope():
    code, out = record("--gate", "関門 1", "--what", "設計の条件が違う", "--issue", "5", "--issue", "3")
    assert (code, out["status"], out["metrics"]["path"]) == (0, "ok", str(ledger()))
    expected = {
        "at": AT,
        "repo": "o/r",
        "gate": "関門 1",
        "what": "設計の条件が違う",
        "sprint": None,
        "issues": [3, 5],
        "prs": [],
        "by": "user",
        "outcome": "rejected",
    }
    assert out["items"] == [expected] and rows() == [expected]
    record("--gate", "関門 2", "--what", "本番", "--pr", "9")
    assert [r["gate"] for r in rows()] == ["関門 1", "関門 2"]  # 追記だけで、前の行は残る


@pytest.mark.parametrize(
    "args",
    [
        ("--gate", "関門 1", "--what", "x"),  # 範囲が無い
        ("--gate", "関門 1", "--what", "  ", "--issue", "1"),  # 要約が空白だけ
        ("--gate", "関門 1", "--issue", "1"),  # 要約が無い
    ],
)
def test_record_without_scope_or_summary_is_two_and_writes_nothing(args):
    code, _ = record(*args)
    assert code == 2 and not ledger().exists()


def test_record_that_cannot_write_is_one(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(blocker / "sub"))  # ファイルの下にディレクトリは作れない
    code, out = record("--gate", "関門 1", "--what", "x", "--issue", "1")
    assert (code, out["status"]) == (1, "stopped") and "書けない" in out["summary"]


def test_count_splits_by_gate_and_scope():
    record("--gate", "関門 1", "--what", "a", "--issue", "1")
    record("--gate", "関門 2", "--what", "b", "--pr", "10")
    record("--gate", "関門 2", "--what", "c", "--issue", "2", "--pr", "11")
    record("--gate", "MVV", "--what", "d", "--issue", "1")
    run("record", "--repo", "x/y", "--gate", "関門 1", "--what", "e", "--issue", "1")  # ほかのリポジトリ
    code, out = run("count", "--repo", "o/r")
    assert (code, out["status"], out["metrics"]) == (0, "ok", {"gate_1": 1, "gate_2": 2, "other": 1, "total": 4, "recorded": True})
    assert out["summary"] == "承認ゲートの差し戻し: 関門 1 が 1 件・関門 2 が 2 件（ほか 1 件）"
    _, out = run("count", "--repo", "o/r", "--issue", "1")
    assert out["metrics"] == {"gate_1": 1, "gate_2": 0, "other": 1, "total": 2, "recorded": True}
    _, out = run("count", "--repo", "o/r", "--issue", "2", "--pr", "10")
    assert out["metrics"] == {"gate_1": 0, "gate_2": 2, "other": 0, "total": 2, "recorded": True}


UNRECORDED = {**dict.fromkeys(gr.COUNT_KEYS), "recorded": False}


@pytest.mark.parametrize("ledger_rows", [0, 1])  # 記録のファイルが無い・ほかの範囲の行だけがある
def test_count_without_any_answer_in_scope_is_unrecorded_not_zero(ledger_rows):
    if ledger_rows:
        record("--gate", "関門 1", "--what", "a", "--issue", "2")
    code, out = run("count", "--repo", "o/r", "--issue", "1")
    assert (code, out["status"], out["metrics"]) == (3, "stopped", UNRECORDED)
    assert "未記録" in out["summary"] and "0 件" not in out["summary"].replace("0 件とは読まない", "")


def test_count_with_only_approvals_in_scope_is_a_recorded_zero():
    code, out = record("--gate", "関門 1", "--what", "設計", "--issue", "1", "--outcome", "approved")
    assert code == 0 and out["summary"].startswith("関門 1 の承認を記録した")
    code, out = run("count", "--repo", "o/r", "--issue", "1")
    assert (code, out["status"], out["items"]) == (0, "ok", [])
    assert out["metrics"] == {"gate_1": 0, "gate_2": 0, "other": 0, "total": 0, "recorded": True}


def test_rows_without_an_outcome_count_as_rejections():
    """#2183 より前に書いた行（outcome が無い）は、今と同じく差し戻しとして数える。"""
    ledger().parent.mkdir(parents=True, exist_ok=True)
    old = {"at": AT, "repo": "o/r", "gate": "関門 2", "what": "x", "sprint": None, "issues": [], "prs": [7], "by": "user"}
    ledger().write_text(json.dumps(old, ensure_ascii=False) + "\n")
    code, out = run("count", "--repo", "o/r", "--pr", "7")
    assert (code, out["status"], out["metrics"]["gate_2"]) == (0, "ok", 1)


@pytest.mark.parametrize("bad", ["{broken\n", "[1]\n"])
def test_count_with_a_broken_row_is_unknown_not_zero(bad):
    record("--gate", "関門 1", "--what", "a", "--issue", "1")
    with ledger().open("a", encoding="utf-8") as f:
        f.write(bad)
    code, out = run("count", "--repo", "o/r")
    assert code == 2 and out["metrics"] == dict.fromkeys(gr.COUNT_KEYS)
    assert out["summary"].startswith("承認ゲートの差し戻し: 不明（")


def test_a_worktree_without_origin_counts_from_the_main_directory(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    code, _ = run("record", "--gate", "関門 1", "--what", "a", "--issue", "1", cwd=tmp_path / "wt")
    assert code == 0 and rows()[0]["repo"] == str(main.resolve())
    _, out = run("count", "--issue", "1", cwd=main)
    assert out["metrics"]["total"] == 1


def _state(tmp_path: Path, pace: str) -> Path:
    state = tmp_path / "s" / "m.json"
    code, _ = run("init", str(state), "--name", "m", "--issue", "4", "--pace", pace, script=STATE_PY, cwd=tmp_path)
    assert code == 0
    return state


def _gate(tmp_path: Path, state: Path, *args: str) -> tuple[int, dict]:
    log = str(tmp_path / "mvv-gate.jsonl")
    return run(
        "gate", str(state), "関門 1", "--what", "設計", "--at", AT, "--mvv-log", log, "--root", str(tmp_path), *args, script=STATE_PY
    )


def test_sprint_state_rejection_on_a_normal_pace_writes_the_ledger(tmp_path):
    state = _state(tmp_path, "normal")
    before = state.read_text()
    code, _ = _gate(tmp_path, state, "--outcome", "rejected")
    assert code == 0 and state.read_text() == before  # 状態ファイルへ rejections を書かない（I6）
    assert rows() == [
        {
            "at": AT,
            "repo": str(tmp_path.resolve()),
            "gate": "関門 1",
            "what": "設計",
            "sprint": str(state.resolve()),
            "issues": [4],
            "prs": [],
            "by": "user",
            "outcome": "rejected",
        }
    ]
    _, out = run("count", "--sprint", str(state), "--root", str(tmp_path))
    assert (out["status"], out["metrics"]["gate_1"]) == ("ok", 1)


def test_sprint_state_rejection_that_cannot_write_is_one(tmp_path, monkeypatch):
    state = _state(tmp_path, "normal")
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(blocker / "sub"))
    code, out = _gate(tmp_path, state, "--outcome", "rejected")
    assert (code, out["status"]) == (1, "stopped") and "記録できない" in out["summary"]


def test_sprint_state_approval_writes_an_approval_not_a_rejection(tmp_path):
    state = _state(tmp_path, "normal")
    _, out = run("count", "--sprint", str(state), "--root", str(tmp_path))
    assert out["status"] == "stopped" and out["metrics"] == UNRECORDED  # 答える前は未記録
    assert _gate(tmp_path, state, "--outcome", "approved")[0] == 0
    assert [r["outcome"] for r in rows()] == ["approved"]
    code, out = run("count", "--sprint", str(state), "--root", str(tmp_path))
    assert (code, out["status"], out["metrics"]["gate_1"], out["metrics"]["gate_2"]) == (0, "ok", 0, 0)


def test_a_gate_passed_by_mvv_in_the_state_file_is_a_recorded_zero(tmp_path):
    """MVV 判定で通した承認ゲートは状態ファイルにだけ残る。それも答えの記録として読み、0 件を返す。"""
    state = _state(tmp_path, "normal")
    m = json.loads(state.read_text())
    m["gates"] = [{"name": "関門 2", "what": "x", "at": AT, "by": "mvv", "verdict": "従う"}]
    state.write_text(json.dumps(m, ensure_ascii=False))
    code, out = run("count", "--sprint", str(state), "--root", str(tmp_path))
    assert (code, out["status"], out["metrics"]["total"]) == (0, "ok", 0)


def test_count_with_an_unreadable_state_file_is_unknown(tmp_path):
    state = tmp_path / "broken.json"
    state.write_text("{broken")
    code, out = run("count", "--repo", "o/r", "--sprint", str(state))
    assert (code, out["status"], out["metrics"]) == (2, "stopped", dict.fromkeys(gr.COUNT_KEYS))


def test_signals_reader_still_skips_broken_rows(tmp_path):
    """決定 4: 共有の読み方へ移しても、覆しの記録の読みは壊れた行を飛ばす。"""
    path = tmp_path / "s.jsonl"
    path.write_text('{"a": 1}\n{broken\n[2]\n\n{"b": 2}\n')
    assert pms.read_jsonl(path) == [{"a": 1}, {"b": 2}]
    assert pms.read_jsonl(tmp_path / "none.jsonl") == []
