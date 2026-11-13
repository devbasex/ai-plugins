"""hook_lib/unattended.py と `hook.py unattended-*`、hooks/claude.json の登録の行の試験（#3242）。

人のいない実行の印・差し戻しの回数・忙しい Stop・失敗の扱いと、`NDF_FACTORY_RUN` が空なら Python を起動しないことを縛る。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import limits  # noqa: E402
import unattended_report as ur  # noqa: E402
from hook_lib import unattended as un  # noqa: E402

SID = "sess-1"
NO_REPORT = "## 確認したいこと\n\n「進めて」と返してもらえれば続けます。\n"
DONE = "## 区間の報告\n\n- 結果: 完了\n- 課題: #12\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "state"))
    limits.use(None)
    return tmp_path / "state" / un.MARK_DIR_NAME


def _start(prompt: str = "/ndf:development-workflow #12", sid: object = SID):
    return un.mark_session({"session_id": sid, "prompt": prompt}, 0.0)


def _stop(text: str = NO_REPORT, **extra) -> dict | None:
    return un.review_stop({"session_id": SID, "last_assistant_message": text, **extra})


def _count(home: Path) -> int:
    mark = un.read_mark(home / f"{SID}.json")
    assert mark is not None
    return mark.pushbacks


# --- 対象の判定と印のパス ---


@pytest.mark.parametrize(
    "prompt",
    [
        "/ndf:development-workflow #12",
        "/goal /ndf:development-workflow #12",
        "  /ndf:development-workflow  \n2 行目",
        "/ndf:development-workflow",
    ],
)
def test_is_target_accepts_the_conductor_prompt(prompt):
    assert un.is_target(prompt)


@pytest.mark.parametrize(
    "prompt",
    ["/ndf:design #1", "課題を進めて", "前置き\n/ndf:development-workflow #1", "/ndf:development-workflowx #1", None, 3, ""],
)
def test_is_target_refuses_other_prompts(prompt):
    assert not un.is_target(prompt)


def test_mark_path_lives_under_the_state_home(home: Path):
    assert un.mark_path(SID) == home / f"{SID}.json"
    assert un.mark_path("a" * 128) is not None


@pytest.mark.parametrize("sid", ["a" * 129, "a/b", "a.b", "", None, 3])
def test_mark_path_refuses_odd_session_ids(home: Path, sid):
    assert un.mark_path(sid) is None


# --- 印の読み書きと掃除 ---


def test_write_and_read_mark_round_trip(home: Path):
    path = home / f"{SID}.json"
    un.write_mark(path, un.Mark(SID, "2026-11-13T00:00:00+00:00", 0))
    assert un.read_mark(path) == un.Mark(SID, "2026-11-13T00:00:00+00:00", 0)


@pytest.mark.parametrize("body", [None, "{", json.dumps({"session_id": SID, "started_at": "t", "pushbacks": "1"})])
def test_read_mark_is_none_for_missing_or_broken_marks(home: Path, body):
    path = home / f"{SID}.json"
    if body is not None:
        path.parent.mkdir(parents=True)
        path.write_text(body, encoding="utf-8")
    assert un.read_mark(path) is None


def test_write_mark_raises_and_leaves_no_temp_file(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        un.write_mark(blocker / "m.json", un.Mark(SID, "t", 0))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file"]


def test_prune_marks_removes_only_marks_past_the_age(tmp_path: Path):
    old, edge, new = (tmp_path / f"{n}.json" for n in ("old", "edge", "new"))
    for p, mtime in ((old, 0), (edge, 50), (new, 90)):
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (mtime, mtime))
    assert un.prune_marks(tmp_path, 100.0, 50.0) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["edge.json", "new.json"]


# --- 差し戻しの判定 ---


@pytest.mark.parametrize(
    ("errors", "count", "busy", "want"),
    [
        ([], 2, False, (False, 0)),
        (["e"], 0, False, (True, 1)),
        (["e"], 1, False, (True, 2)),
        (["e"], 2, False, (False, 2)),
        (["e"], 1, True, (False, 1)),
    ],
)
def test_next_count(errors, count, busy, want):
    assert un.next_count(errors, count, 2, busy) == want


def test_pushback_reason_names_the_errors_the_contract_and_the_rest():
    text = un.pushback_reason(["結果の行が無い"], 0)
    assert "結果の行が無い" in text and un.FORM_REL in text and "0" in text


# --- start / stop / end ---


def test_start_marks_the_conductor_and_returns_the_guide(home: Path):
    assert _start() == un.GUIDE
    assert _count(home) == 0


def test_start_removes_marks_past_the_age_and_keeps_the_new_one(home: Path):
    home.mkdir(parents=True)
    stale = home / "old-1.json"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (0, 0))
    assert un.mark_session({"session_id": SID, "prompt": "/ndf:development-workflow #1"}, limits.seconds(un.MARK_POLICY) + 1.0)
    assert sorted(p.name for p in home.iterdir()) == [f"{SID}.json"]


@pytest.mark.parametrize(("prompt", "sid"), [("/ndf:design #1", SID), ("/ndf:development-workflow #1", "a/b")])
def test_start_leaves_other_sessions_unmarked(home: Path, prompt, sid):
    assert _start(prompt, sid) is None
    assert not home.exists() or not list(home.iterdir())


def test_stop_pushes_back_twice_in_a_row_then_lets_the_third_through(home: Path):
    _start()
    first = _stop()
    assert first is not None and first["decision"] == "block" and ur.ERR_NO_RESULT in first["reason"]
    assert _count(home) == 1
    assert _stop(stop_hook_active=True) is not None
    assert _count(home) == 2
    assert _stop(stop_hook_active=True) is None
    assert _count(home) == 2 and (home / f"{SID}.json").exists()


def test_a_fitting_stop_resets_the_count_and_keeps_the_mark(home: Path):
    _start()
    _stop()
    _stop()
    assert _stop(DONE) is None
    assert _count(home) == 0
    assert _stop() is not None and _count(home) == 1


@pytest.mark.parametrize("extra", [{"background_tasks": [{"id": "b1"}]}, {"session_crons": [{"id": "c1"}]}])
def test_a_busy_stop_is_not_pushed_back_and_keeps_the_count(home: Path, extra):
    _start()
    _stop()
    assert _stop(**extra) is None
    assert _count(home) == 1


def test_stop_without_a_mark_does_nothing(home: Path):
    assert _stop() is None
    assert not home.exists()


def test_stop_lets_through_when_the_contract_cannot_be_read(home: Path, monkeypatch, capsys):
    _start()

    def broken():
        raise ur.FormError("壊れている")

    monkeypatch.setattr(un.ur, "load_form", broken)
    assert _stop() is None
    assert _count(home) == 0
    assert "ndf: unattended-stop: 区間の報告の契約を読めない" in capsys.readouterr().err


def test_stop_lets_through_when_the_limits_cannot_be_read(home: Path, tmp_path: Path, capsys):
    _start()
    bad = tmp_path / "limits.json"
    bad.write_text(json.dumps({"version": 1, "policies": {"x": {"summary": "s", "seconds": 1}}}), encoding="utf-8")
    limits.use(bad)
    try:
        assert _stop() is None
    finally:
        limits.use(None)
    assert _count(home) == 0
    assert "ndf: unattended-stop:" in capsys.readouterr().err


def test_stop_still_pushes_back_when_the_count_cannot_be_written(home: Path, monkeypatch, capsys):
    _start()

    def refuse(path, mark):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(un, "write_mark", refuse)
    assert _stop() is not None
    assert "回数を書けない" in capsys.readouterr().err


def test_end_removes_the_mark_and_tolerates_none(home: Path):
    _start()
    un.clear_mark({"session_id": SID})
    assert not (home / f"{SID}.json").exists()
    un.clear_mark({"session_id": SID})


# --- hook.py の副命令と登録の行 ---


def _hook(sub: str, payload, env: dict, *flags: str) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, *flags, str(SCRIPTS / "hook.py"), sub], input=stdin, capture_output=True, text=True, env=env, timeout=30
    )


def _env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("NDF_FACTORY_RUN", "XDG_STATE_HOME")}
    return {**env, "NDF_STATE_HOME": str(tmp_path / "state"), "HOME": str(tmp_path / "home"), **extra}


def test_the_subcommands_run_without_site_packages(tmp_path: Path):
    """hook 用の仮想環境が無くても `python3 -S` で 3 つの副命令が動く（標準ライブラリだけの経路）。"""
    env = _env(tmp_path)
    got = _hook("unattended-start", {"session_id": SID, "prompt": "/ndf:development-workflow #1"}, env, "-S")
    assert got.returncode == 0
    assert json.loads(got.stdout)["hookSpecificOutput"] == {"hookEventName": "UserPromptSubmit", "additionalContext": un.GUIDE}
    stop = _hook("unattended-stop", {"session_id": SID, "last_assistant_message": NO_REPORT}, env, "-S")
    assert stop.returncode == 0 and json.loads(stop.stdout)["decision"] == "block"
    end = _hook("unattended-end", {"session_id": SID}, env, "-S")
    assert end.returncode == 0 and end.stdout == ""
    assert not (tmp_path / "state" / un.MARK_DIR_NAME / f"{SID}.json").exists()


@pytest.mark.parametrize("sub", ["unattended-start", "unattended-stop", "unattended-end"])
def test_the_subcommands_print_nothing_for_non_json_input(tmp_path: Path, sub):
    got = _hook(sub, "not json", _env(tmp_path))
    assert (got.returncode, got.stdout) == (0, "")


def test_start_failure_is_one_stderr_line_and_exit_0(tmp_path: Path):
    blocker = tmp_path / "state"
    blocker.write_text("x", encoding="utf-8")
    got = _hook("unattended-start", {"session_id": SID, "prompt": "/ndf:development-workflow #1"}, _env(tmp_path))
    assert (got.returncode, got.stdout) == (0, "")
    assert got.stderr.startswith("ndf: unattended-start: ")


def _registered() -> dict[str, str]:
    hooks = json.loads((PLUGIN / "hooks" / "claude.json").read_text(encoding="utf-8"))["hooks"]
    found = {}
    for event, groups in hooks.items():
        for group in groups:
            for h in group["hooks"]:
                if "unattended-" in h["command"]:
                    found[event] = h["command"]
    return found


def test_the_three_events_are_registered():
    assert set(_registered()) == {"UserPromptSubmit", "Stop", "SessionEnd"}


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop", "SessionEnd"])
def test_the_registered_line_starts_no_python_without_the_factory_variable(tmp_path: Path, event):
    """`NDF_FACTORY_RUN` が空なら sh だけで終える（プラグインの根に python の起動の跡が残らない）。"""
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    trace = tmp_path / "ran"
    (root / "scripts" / "hook.py").write_text(f"open({str(trace)!r}, 'w').write('ran')\n", encoding="utf-8")
    for factory in ("", None):
        env = _env(tmp_path, CLAUDE_PLUGIN_ROOT=str(root))
        if factory is not None:
            env["NDF_FACTORY_RUN"] = factory
        got = subprocess.run(_registered()[event], shell=True, input="{}", capture_output=True, text=True, env=env, timeout=30)
        assert (got.returncode, got.stdout) == (0, "")
    assert not trace.exists()


def test_the_registered_line_marks_without_the_hook_environment(tmp_path: Path):
    """hook 用の仮想環境（`~/.cache/ndf/roots/...`）が無くても、登録の行が印を作り案内を返す。"""
    env = _env(tmp_path, CLAUDE_PLUGIN_ROOT=str(PLUGIN), NDF_FACTORY_RUN="1")
    payload = json.dumps({"session_id": SID, "prompt": "/goal /ndf:development-workflow #1"})
    got = subprocess.run(_registered()["UserPromptSubmit"], shell=True, input=payload, capture_output=True, text=True, env=env, timeout=30)
    assert got.returncode == 0
    assert json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"] == un.GUIDE
    assert (tmp_path / "state" / un.MARK_DIR_NAME / f"{SID}.json").exists()
