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
# Claude Code が文脈へそのまま入れる上限（10,000 文字）の半分
MAX_TEXT_CHARS = 5000


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


def test_pushback_reason_names_the_errors_the_contract_the_rest_and_the_form():
    form = ur.load_form()
    text = un.pushback_reason([ur.ERR_NO_RESULT], 0, form)
    assert ur.ERR_NO_RESULT in text and un.FORM_REL in text and "残り 0 回" in text
    assert ur.describe_form(form) in text


# 誤りの種類ごとの応答（結果の行が無い・値が契約の外・要る項目の欠け・材料の誤り・`次のコマンド:` の残り）
WRONG_TEXTS = {
    "no-result": NO_REPORT,
    "bad-value": "## 区間の報告\n\n- 結果: 続けられない\n- 理由: Bash が拒まれた\n",
    "missing": "## 区間の報告\n\n- 結果: できなかった\n- 理由: 無し\n",
    "material": "## 区間の報告\n\n- 結果: 判断が要る\n- 区分: ndf-ask:cost\n- 問い: 呼んでよいか\n- 材料: /tmp/x.md\n",
    "next-command": "次のコマンド: python3 run.py\n\n## 区間の報告\n\n- 結果: できなかった\n- 理由: 途中で止めた\n",
}


@pytest.mark.parametrize("kind", sorted(WRONG_TEXTS))
def test_every_kind_of_pushback_carries_the_form(home: Path, kind):
    """誤りの種類に関わらず、差し戻しの文は契約の値・意味・要る項目・条件・材料の書き方を載せる（I1）。"""
    _start()
    got = _stop(WRONG_TEXTS[kind])
    assert got is not None and got["decision"] == "block"
    form = ur.load_form()
    assert ur.describe_form(form) in got["reason"]
    for value in form.values:
        assert value in got["reason"] and form.meanings[value] in got["reason"]
        rule = form.required[value]
        for name in rule.always:
            assert name in got["reason"]
        for cond in rule.when:
            assert cond.item in got["reason"] and (cond.prefix or cond.equals) in got["reason"]
            assert all(n in got["reason"] for n in cond.need)


def test_a_report_copied_from_the_pushback_fits_the_contract(home: Path):
    """差し戻しの文の例だけから書いた区間の報告が判定で誤り 0 件になり、差し戻されない（受け入れ条件 3）。"""
    _start()
    reason = _stop()["reason"]
    blocks = reason.split("```markdown\n")[1:]
    form = ur.load_form()
    assert len(blocks) == len(form.values)
    for block in blocks:
        example = block.split("```")[0]
        assert ur.result_value(example) in form.values
        assert ur.report_errors(example, form) == []
        assert _stop(example) is None


def test_a_pushback_with_every_kind_of_error_stays_short():
    errors = [
        ur.ERR_NO_RESULT,
        ur.ERR_BAD_VALUE.format(values="判断が要る / 完了 / できなかった", value="続けられない"),
        ur.ERR_MISSING.format(item="理由", value="できなかった"),
        ur.ERR_BAD_MATERIAL.format(item="材料", body="本文", url_prefix="https://", value="/tmp/x.md"),
        un.ERR_NEXT_COMMAND,
    ]
    assert len(un.pushback_reason(errors, 2, ur.load_form())) <= MAX_TEXT_CHARS


def test_start_guide_adds_the_form_and_stays_short():
    form = ur.load_form()
    guide = un.start_guide(form)
    assert guide.startswith(un.GUIDE) and ur.describe_form(form) in guide
    assert len(guide) <= MAX_TEXT_CHARS
    assert un.start_guide(None) == un.GUIDE


# --- start / stop / end ---


def test_start_marks_the_conductor_and_returns_the_guide(home: Path):
    assert _start() == un.start_guide(ur.load_form())
    assert _count(home) == 0


def test_start_returns_the_plain_guide_when_the_contract_cannot_be_read(home: Path, monkeypatch, capsys):
    def broken():
        raise ur.FormError("壊れている")

    monkeypatch.setattr(un.ur, "load_form", broken)
    assert _start() == un.GUIDE
    assert _count(home) == 0
    assert "ndf: unattended-start: 区間の報告の契約を読めない" in capsys.readouterr().err


def _count_loads(monkeypatch) -> list[int]:
    calls = [0]
    real = ur.load_form

    def counted(*args, **kwargs):
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(un.ur, "load_form", counted)
    return calls


def test_start_reads_no_contract_for_other_prompts(home: Path, monkeypatch):
    calls = _count_loads(monkeypatch)
    assert _start("/ndf:design #1") is None
    assert calls == [0]


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


def test_a_pushback_reads_the_contract_once_and_uses_it(home: Path, monkeypatch):
    _start()
    calls = _count_loads(monkeypatch)
    got = _stop()
    assert calls == [1]
    form = ur.load_form()
    assert got == {"decision": "block", "reason": un.pushback_reason(ur.report_errors(NO_REPORT, form), 1, form)}


def test_after_the_limit_a_bad_value_passes_and_leaves_one_stderr_line(home: Path, capsys):
    """上限まで差し戻した後の契約の外の値は通り、回数は上限のまま、誤りの一覧が標準エラーに 1 行残る（I5）。"""
    _start()
    limit = int(limits.value(un.PUSHBACK_POLICY, "attempts"))
    for _ in range(limit):
        assert _stop() is not None
    capsys.readouterr()
    assert _stop(WRONG_TEXTS["bad-value"]) is None
    assert _count(home) == limit
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1
    assert err[0].startswith(f"ndf: unattended-stop: 差し戻しの上限（{limit} 回）に達したため通す: ")
    assert "続けられない" in err[0]


@pytest.mark.parametrize("extra", [{"text": DONE}, {"background_tasks": [{"id": "b1"}]}])
def test_passing_stops_leave_no_limit_line(home: Path, capsys, extra):
    _start()
    text = extra.pop("text", NO_REPORT)
    assert _stop(text, **extra) is None
    assert "上限" not in capsys.readouterr().err


def test_a_fitting_stop_resets_the_count_and_keeps_the_mark(home: Path):
    _start()
    _stop()
    _stop()
    assert _stop(DONE) is None
    assert _count(home) == 0
    assert _stop() is not None and _count(home) == 1


STALLED = (
    "mode: standard\n根拠: 本番の振る舞いを変える\n"
    '次のコマンド: python3 "$SCRIPTS/supervise.py" new sprint --name m6 --worktree /repo\n'
    "  --issue 12 --design 12 --out /work/sprint-m6\n\n"
    "## 区間の報告\n\n- 結果: できなかった\n- 課題: #12\n- 理由: スプリントのプランはまだ書き出していない\n"
)


def test_a_stop_that_leaves_the_next_command_unrun_is_pushed_back(home: Path):
    _start()
    got = _stop(STALLED)
    assert got is not None and got["decision"] == "block" and un.ERR_NEXT_COMMAND in got["reason"]
    assert _count(home) == 1


@pytest.mark.parametrize(
    "text",
    [
        STALLED.replace("できなかった", "完了"),
        "## 判断の材料\n\n要点。\n\n"
        + STALLED.replace("- 結果: できなかった", "- 結果: 判断が要る\n- 区分: ndf-ask:cost\n- 問い: 有料の API を呼ぶか\n- 材料: 本文"),
        STALLED.replace("次のコマンド: python3", "次のコマンド: 無し（supervisor で回す: 設計）\n# python3"),
        STALLED.replace("次のコマンド:", "前に打ったコマンド:"),
        STALLED.replace("- 理由:", "- 区分: ndf-ask:human-action\n- 人に頼むこと: 試しの系へ対話でログインする\n- 理由:"),
    ],
)
def test_the_next_command_rule_lets_other_stops_through(home: Path, text):
    _start()
    assert _stop(text) is None
    assert _count(home) == 0


def test_pending_command_errors_reads_only_a_line_with_a_command():
    assert un.pending_command_errors(STALLED) == [un.ERR_NEXT_COMMAND]
    assert un.pending_command_errors("- 次のコマンド: `/ndf:release`\n- 結果: できなかった\n") == [un.ERR_NEXT_COMMAND]
    assert un.pending_command_errors("次のコマンド:\n- 結果: できなかった\n") == []
    assert un.pending_command_errors("- 次のコマンド: `無し（supervisor で回す: 設計）`\n- 結果: できなかった\n") == []
    assert un.pending_command_errors("- 次のコマンド: ``\n- 結果: できなかった\n") == []
    assert un.pending_command_errors("次のコマンド: /ndf:release\n- 結果: 完了\n") == []
    assert un.pending_command_errors(None) == []


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
    want = un.start_guide(ur.load_form())
    assert json.loads(got.stdout)["hookSpecificOutput"] == {"hookEventName": "UserPromptSubmit", "additionalContext": want}
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
    assert json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"] == un.start_guide(ur.load_form())
    assert (tmp_path / "state" / un.MARK_DIR_NAME / f"{SID}.json").exists()
