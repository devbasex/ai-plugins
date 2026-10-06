"""参加の確認（`lib/auth.py`）のテスト。

主題は止めない確認 `probe_auth`（#727）である。失敗しても例外を上げず、`ok` と理由を
返し、`NDF_SKIP_AUTH_CHECK` が立てば確認のコマンドを 1 回も呼ばない。#1290 で、認証確認に
最小の呼び出しと既定のモデルでの引き直しを足した（AC1〜AC8・AC16・AC18）。
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"

# #461 と #1589 の実物の行
LINE_404 = (
    "ERROR: unexpected status 404 Not Found: The model `gpt-5.5` does not exist or you do not have access to it., "
    "url: https://chatgpt.com/backend-api/codex/responses"
)
LINE_REVOKED = "ERROR: Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in again."
LINE_400 = (
    'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":'
    "\"The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.\"}}"
)


def _load_auth():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("ndf_lib_auth_probe", LIB / "auth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def auth():
    return _load_auth()


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _fake_run(auth, monkeypatch, answer):
    """`answer(cmd) -> CompletedProcess` で `subprocess.run` を置き換え、呼ばれたコマンドを返す。"""
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        return answer(list(cmd))

    monkeypatch.setattr(auth.subprocess, "run", run)
    return calls


# ---------- 失敗の形（起動できない・時間切れ・終了コード・未認証の文言） ----------


def test_probe_reports_a_missing_command(auth, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError(args[0][0])

    monkeypatch.setattr(auth.subprocess, "run", missing)
    messages: list[str] = []

    results, skipped = auth.probe_auth(["codex"], info=messages.append, env={})

    assert skipped is False
    assert results["codex"]["ok"] is False
    assert results["codex"]["reason"] == "missing_cli"
    assert results["codex"]["detail"] == "コマンドが見つかりません"
    assert results["codex"]["command"] == "codex login status"
    assert len(messages) == 1 and messages[0].startswith("❌ codex: missing_cli — コマンドが見つかりません")


def test_probe_reports_a_command_it_cannot_start_with_a_real_unreadable_path(auth, monkeypatch, tmp_path):
    """読めないディレクトリだけの PATH で、確認コマンドが見つからないとき（#813）。

    権限が効かない実行者（root）では条件が成り立たないため、その場合は飛ばす。
    """
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0o000)
    try:
        try:
            list(unreadable.iterdir())
            pytest.skip("権限が効かない実行者のため、読めない PATH を再現できない")
        except PermissionError:
            pass
        monkeypatch.setenv("PATH", str(unreadable))

        results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})
    finally:
        unreadable.chmod(0o700)

    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == "コマンドを実行できません（Permission denied）"


def test_probe_reports_a_command_it_cannot_start(auth, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(auth.subprocess, "run", denied)

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == "コマンドを実行できません（Permission denied）"


def test_probe_reports_a_command_that_is_not_an_executable_format(auth, monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text("\x7fnot an executable\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == "コマンドを実行できません（Exec format error）"


def test_probe_reports_a_timeout(auth, monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(auth.subprocess, "run", time_out)

    results, skipped = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert skipped is False
    assert results["codex"]["ok"] is False
    assert results["codex"]["reason"] == "timeout"
    assert results["codex"]["detail"] == f"{auth.AUTH_PROBE_TIMEOUT} 秒の持ち時間で応答しませんでした"


def test_the_time_budget_is_shared_by_the_steps_of_one_runtime(auth, monkeypatch):
    """1 者の持ち時間は 3 種類の確認で共有する（決定 8）。後の種類ほど残りの秒数が短い。"""
    budgets: list[float] = []

    def run(cmd, **kw):
        budgets.append(kw["timeout"])
        time.sleep(0.05)
        return _completed(cmd, 0, stdout="OK")

    monkeypatch.setattr(auth.subprocess, "run", run)

    auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert len(budgets) == 2
    assert budgets[0] <= auth.AUTH_PROBE_TIMEOUT
    assert budgets[1] < budgets[0]


def test_probe_reports_a_nonzero_exit_of_the_auth_step_as_unauthenticated(auth, monkeypatch):
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 1, stderr="error: no session\n"))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is False
    assert results["codex"]["reason"] == "unauthenticated"
    assert results["codex"]["detail"] == "error: no session"
    assert calls == [["codex", "login", "status"]]


def test_an_unauthenticated_cli_runs_no_minimal_call(auth, monkeypatch):
    """AC18・I5: 未認証の文言に当たれば外れ、最小の呼び出しを走らせない。kiro は終了コード 0 でも文言で拾う。"""
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 0, stdout="Not logged in\n"))

    results, _ = auth.probe_auth(["kiro"], info=lambda _m: None, env={})

    assert results["kiro"]["ok"] is False
    assert results["kiro"]["reason"] == "unauthenticated"
    assert results["kiro"]["detail"] == "Not logged in"
    assert calls == [["kiro-cli", "whoami"]]


# ---------- 最小の呼び出し（AC1・AC2・AC7） ----------


def _codex(model_answer, default_answer=None):
    """認証確認は通り、種類 model / default が別の答えを返す偽の codex。"""

    def answer(cmd):
        if cmd[:3] == ["codex", "login", "status"]:
            return _completed(cmd, 0, stdout="Logged in using ChatGPT")
        if "--ignore-user-config" in cmd:
            return default_answer(cmd)
        return model_answer(cmd)

    return answer


def test_a_cli_that_cannot_reach_its_model_is_unavailable(auth, monkeypatch):
    """AC1: 認証は通るがモデルを引けない（#461 の 404）。明示が無くても種類 default も通らなければ外れる。"""
    fail = lambda cmd: _completed(cmd, 1, stderr=f"model: gpt-5.5\n{LINE_404}\n")  # noqa: E731
    _fake_run(auth, monkeypatch, _codex(fail, fail))
    messages: list[str] = []

    results, _ = auth.probe_auth(["codex"], info=messages.append, env={})

    assert results["codex"]["ok"] is False
    assert results["codex"]["reason"] == "model_unavailable"
    assert results["codex"]["detail"].startswith("ERROR: unexpected status 404 Not Found")
    assert messages[0].startswith("❌ codex: model_unavailable — ERROR: unexpected status 404")


def test_a_cli_with_a_revoked_refresh_token_is_unavailable(auth, monkeypatch):
    """AC2: 認証の状態確認は通るが更新トークンが失効している（#461 の PR #661）。種類 default へ進まない。"""
    lines = (
        'ERROR codex_login::auth::manager: Failed to refresh token: 401 Unauthorized: {"error": '
        '{"message": "Your session has ended.", "code": "refresh_token_invalidated"}}\n' + LINE_REVOKED + "\n"
    )
    calls = _fake_run(auth, monkeypatch, _codex(lambda cmd: _completed(cmd, 1, stderr=lines)))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["reason"] == "auth_expired"
    assert results["codex"]["detail"] == LINE_REVOKED
    assert not any("--ignore-user-config" in c for c in calls)


def test_a_passing_cli_says_it_checked_auth_and_model(auth, monkeypatch):
    """AC3・AC15: 通った者は「認証とモデル」の 1 行で、今の 3 キーを保つ。"""
    _fake_run(auth, monkeypatch, _codex(lambda cmd: _completed(cmd, 0, stdout="OK", stderr="model: gpt-6.1-sol\n")))
    messages: list[str] = []

    results, skipped = auth.probe_auth(["codex"], info=messages.append, env={})

    r = results["codex"]
    assert skipped is False
    assert (r["ok"], r["reason"], r["level"], r["model"], r["default_model"]) == (True, "ok", "model", "gpt-6.1-sol", None)
    assert r["command"].startswith("codex login status && codex exec")
    assert {"command", "ok", "detail"} <= set(r)
    assert messages[0].startswith("✅ codex: 認証とモデル（")


def test_a_cli_without_a_minimal_call_is_checked_by_auth_only(auth, monkeypatch):
    """agy は最小の呼び出しを持たない（決定 1）。認証確認だけで入る。"""
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 0, stdout="gemini"))
    messages: list[str] = []

    results, _ = auth.probe_auth(["agy"], info=messages.append, env={})

    assert (results["agy"]["ok"], results["agy"]["level"]) == (True, "auth")
    assert calls == [["agy", "models"]]
    assert messages[0].startswith("✅ agy: 認証だけ（モデルの確認を持たない）")


def test_the_auth_level_runs_the_auth_step_only(auth, monkeypatch):
    """external-ai の `run` は認証確認だけを通す（決定 9）。"""
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 0, stdout="ok"))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={}, level="auth")

    assert (results["codex"]["ok"], results["codex"]["level"]) == (True, "auth")
    assert calls == [["codex", "login", "status"]]


def test_a_usage_limit_during_the_check_passes(auth, monkeypatch):
    """決定 6: 最小の呼び出しが利用上限を返しても通す（上限の扱いは #919 の規則に任せる）。"""
    _fake_run(auth, monkeypatch, _codex(lambda cmd: _completed(cmd, 1, stderr="ERROR: You've hit your usage limit.\n")))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is True


def test_an_explicit_model_that_cannot_be_reached_is_not_switched(auth, monkeypatch):
    """AC7: `--model codex=<名前>` で明示したモデルを引けなければ、種類 default を呼ばずに外す。"""
    calls = _fake_run(
        auth,
        monkeypatch,
        _codex(lambda cmd: _completed(cmd, 1, stderr=LINE_400 + "\n"), lambda cmd: _completed(cmd, 0, stdout="OK")),
    )

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={}, models={"codex": "gpt-6.1-sol"})

    assert results["codex"]["reason"] == "model_unavailable"
    assert ["--model", "gpt-6.1-sol"] == calls[1][-2:]
    assert not any("--ignore-user-config" in c for c in calls)


def test_kiro_rejecting_an_explicit_model_is_unavailable(auth, monkeypatch):
    """決定 3: kiro に明示したモデルが `failed to set model` を返したら外す。明示しなければ問わない。"""

    def answer(cmd):
        if cmd[:2] == ["kiro-cli", "whoami"]:
            return _completed(cmd, 0, stdout="user@example.com")
        warn = "\x1b[33m[warn] failed to set model 'x': Method not found\x1b[0m\n" if "--model" in cmd else ""
        return _completed(cmd, 0, stdout="OK\n", stderr=warn)

    _fake_run(auth, monkeypatch, answer)

    explicit, _ = auth.probe_auth(["kiro"], info=lambda _m: None, env={}, models={"kiro": "x"})
    implicit, _ = auth.probe_auth(["kiro"], info=lambda _m: None, env={})

    assert explicit["kiro"]["reason"] == "model_unavailable"
    assert implicit["kiro"]["ok"] is True


def test_kiro_with_an_empty_reply_fails_the_minimal_call(auth, monkeypatch):
    def answer(cmd):
        return _completed(cmd, 0, stdout="user@example.com" if cmd[1] == "whoami" else "  \n")

    _fake_run(auth, monkeypatch, answer)

    results, _ = auth.probe_auth(["kiro"], info=lambda _m: None, env={})

    assert results["kiro"]["reason"] == "probe_failed"


def test_claude_stdout_404_is_model_unavailable(auth, monkeypatch):
    """claude の存在しないモデルは標準出力の JSON の `"api_error_status":404`（2026-10-06 の実測）。"""

    def answer(cmd):
        if cmd[:3] == ["claude", "auth", "status"]:
            return _completed(cmd, 0, stdout='{"loggedIn": true}')
        if "default" in cmd:
            return _completed(cmd, 1, stdout='{"type":"result","is_error":true,"api_error_status":404}')
        return _completed(cmd, 1, stdout='{"type":"result","is_error":true,"api_error_status":404}')

    _fake_run(auth, monkeypatch, answer)

    results, _ = auth.probe_auth(["claude"], info=lambda _m: None, env={})

    assert results["claude"]["reason"] == "model_unavailable"


# ---------- 既定のモデルへの切り替え（AC5・AC8） ----------


def test_a_configured_model_falls_back_to_the_cli_default(auth, monkeypatch):
    """AC5: 設定のモデルが 400 を返し、`--ignore-user-config` では通る codex は既定のモデルで入る。"""
    _fake_run(
        auth,
        monkeypatch,
        _codex(
            lambda cmd: _completed(cmd, 1, stderr="model: gpt-6.1-sol\n" + LINE_400 + "\n"),
            lambda cmd: _completed(cmd, 0, stdout="OK", stderr="model: gpt-6-astra\n"),
        ),
    )
    messages: list[str] = []

    results, _ = auth.probe_auth(["codex"], info=messages.append, env={})

    r = results["codex"]
    assert (r["ok"], r["default_model"], r["from_model"], r["level"]) == (True, "gpt-6-astra", "gpt-6.1-sol", "model")
    assert messages[0].startswith("↪ codex: 設定のモデル gpt-6.1-sol を引けないため、既定のモデル gpt-6-astra で担当に入れる")


def test_claude_falls_back_with_model_default(auth, monkeypatch):
    """決定 2: claude は `--model default` で引き直し、起動の引数も `default` にする。"""

    def answer(cmd):
        if cmd[:3] == ["claude", "auth", "status"]:
            return _completed(cmd, 0, stdout='{"loggedIn": true}')
        if cmd[-2:] == ["--model", "default"]:
            return _completed(cmd, 0, stdout='{"type":"result","result":"OK","modelUsage":{"claude-x":{}}}')
        return _completed(cmd, 1, stdout='{"type":"result","is_error":true,"api_error_status":404}')

    _fake_run(auth, monkeypatch, answer)

    results, _ = auth.probe_auth(["claude"], info=lambda _m: None, env={})

    assert (results["claude"]["ok"], results["claude"]["default_model"]) == (True, "default")


def _write_fake_codex(bin_dir: pathlib.Path, log: pathlib.Path) -> None:
    """設定のモデルを引けず、`--ignore-user-config` では通る偽の codex（PATH に置く）。"""
    script = f"""\
        #!/usr/bin/env bash
        echo "$*" >> {log}
        if [ "$1" = login ]; then echo "Logged in using ChatGPT"; exit 0; fi
        cat >/dev/null
        case " $* " in
          *" --ignore-user-config "*) echo "model: gpt-6-astra" >&2; echo OK; exit 0 ;;
        esac
        echo "model: gpt-5.5" >&2
        echo '{LINE_404}' >&2
        exit 1
        """
    path = bin_dir / "codex"
    path.write_text(textwrap.dedent(script), encoding="utf-8")
    path.chmod(0o755)


def test_the_check_does_not_rewrite_the_user_config(auth, monkeypatch, tmp_path):
    """AC8・I6: 偽の CLI を PATH に置き、確認の前後で偽の HOME の設定ファイルが同じ。"""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    config = home / ".codex" / "config.toml"
    config.write_text('model = "gpt-5.5"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _write_fake_codex(bin_dir, log)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    before = config.read_bytes()

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["default_model"] == "gpt-6-astra"
    assert config.read_bytes() == before
    assert "--ignore-user-config" in log.read_text(encoding="utf-8")


# ---------- 飛ばし・並行・伏せ字 ----------


def test_probe_truncates_detail_to_200_chars(auth, monkeypatch):
    _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 1, stderr="x" * 300))

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert len(results["codex"]["detail"]) == 200


def test_the_detail_hides_tokens(auth, monkeypatch):
    """非機能（セキュリティ）: 詳細のトークンの形の文字列は伏せ字になる。"""
    token = "ghp_" + "a" * 36
    _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 1, stderr=f"error: bad token {token}\n"))
    messages: list[str] = []

    results, _ = auth.probe_auth(["codex"], info=messages.append, env={})

    assert token not in results["codex"]["detail"] and "***" in results["codex"]["detail"]
    assert token not in messages[0]


def test_probe_skips_without_running_any_command(auth, monkeypatch):
    """AC16・I7: `NDF_SKIP_AUTH_CHECK` が立つと確認のコマンドも最小の呼び出しも 1 回も呼ばれない。"""
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 0))
    messages: list[str] = []

    results, skipped = auth.probe_auth(["codex", "agy"], info=messages.append, env={auth.SKIP_ENV: "1"}, models={"codex": "x"})

    assert (results, skipped) == ({}, True)
    assert calls == []
    assert messages == [f"⚠ {auth.SKIP_ENV} が設定されているため参加の確認を飛ばしました"]


def test_probe_ignores_an_unknown_runtime(auth, monkeypatch):
    calls = _fake_run(auth, monkeypatch, lambda cmd: _completed(cmd, 0, stdout="OK"))

    results, skipped = auth.probe_auth(["unknown", "agy"], info=lambda _m: None, env={})

    assert skipped is False
    assert list(results) == ["agy"]
    assert calls == [["agy", "models"]]


def test_probe_never_raises_and_returns_every_runtime(auth, monkeypatch):
    """1 者の失敗で残りの確認が止まらない。出力は `ALL_RUNTIMES` の順。"""

    def answer(cmd):
        if cmd[0] == "codex":
            raise FileNotFoundError(cmd[0])
        return _completed(cmd, 0, stdout="ok")

    _fake_run(auth, monkeypatch, answer)
    messages: list[str] = []

    results, _ = auth.probe_auth(["agy", "codex"], info=messages.append, env={})

    assert results["codex"]["ok"] is False
    assert results["agy"]["ok"] is True
    assert [m.split(":")[0] for m in messages] == ["❌ codex", "✅ agy"]


def test_participants_are_checked_in_parallel(auth, monkeypatch):
    """非機能（性能）: 各 0.6 秒の確認 2 種類 × 2 者で、全体は 1 者の和（1.2 秒）+ 余裕に収まり、秒数が残る。"""

    def answer(cmd):
        time.sleep(0.6)
        return _completed(cmd, 0, stdout="OK")

    _fake_run(auth, monkeypatch, answer)
    started = time.monotonic()

    results, _ = auth.probe_auth(["claude", "codex"], info=lambda _m: None, env={})

    assert time.monotonic() - started < 2.4
    assert all(r["seconds"] >= 1.0 for r in results.values())


# ---------- 確認と監視が同じ文言の表で同じ理由になる（I8） ----------


@pytest.mark.parametrize(
    "line, reason",
    [(LINE_404, "model_unavailable"), (LINE_REVOKED, "auth_expired"), (LINE_400, "model_unavailable")],
)
def test_the_check_and_the_monitor_classify_the_same_line_alike(auth, tmp_path, line, reason):
    import monitor_scan
    import monitor_types

    check = auth._classify_output("model", "codex", None, 1, "", line + "\n")
    err = tmp_path / "x-err.log"
    err.write_text(line + "\n", encoding="utf-8")
    paths = monitor_types.AgentPaths(
        agent="codex",
        pr=1,
        pidfile=tmp_path / "x.pid",
        err_log=err,
        stdout_log=tmp_path / "x-stdout.log",
        progress_log=tmp_path / "x-progress.log",
        result=tmp_path / "x-result.json",
    )

    fatal, _ = monitor_scan._early_error(paths, "codex", disabled=False)

    assert check[0] == reason
    assert fatal is not None and fatal.reason == reason
