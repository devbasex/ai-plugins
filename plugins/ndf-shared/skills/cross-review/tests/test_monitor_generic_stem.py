"""monitor.py 汎用化（作業単位 9）のテスト。

cross-refactoring が同じ監視資産を使えるようにするために足した 4 点を検証する。

1. `--stem-template` で一時ファイル名の骨格を差し替えられる（既定は現行のまま）
2. `--tmp-dir` / `CROSS_REFACTORING_TMP_DIR` で一時ディレクトリを指定できる
3. 照合の前に ANSI エスケープを除去する（kiro は色コードを必ず混ぜる）
4. claude / kiro の早期エラーを検知する

既存テストを 1 つも変更しないことが作業単位 9 の完了条件なので、
ここでは **追加した経路だけ** を見る。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from unittest import mock


# ---------- 1. stem template ----------

def test_default_stem_keeps_cross_review_naming(monitor_mod, tmp_path):
    """既定の骨格は現行の `<agent>-review-pr<PR>` のままであること。"""
    with mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path):
        paths = monitor_mod.AgentPaths.for_("codex", 42)
    assert paths.pidfile == tmp_path / "codex-review-pr42.pid"
    assert paths.result == tmp_path / "codex-review-pr42-result.json"


def test_stem_template_overrides_naming(monitor_mod, tmp_path):
    """`{agent}` と `{id}` を埋めた任意の骨格を使えること。"""
    with mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path):
        paths = monitor_mod.AgentPaths.for_(
            "kiro", 130, "{agent}-propose-rf{id}"
        )
    assert paths.pidfile == tmp_path / "kiro-propose-rf130.pid"
    assert paths.err_log == tmp_path / "kiro-propose-rf130-err.log"
    assert paths.stdout_log == tmp_path / "kiro-propose-rf130-stdout.log"
    assert paths.progress_log == tmp_path / "kiro-propose-rf130-progress.log"
    assert paths.result == tmp_path / "kiro-propose-rf130-result.json"


def test_monitor_agent_uses_stem_template(monitor_mod, tmp_path):
    """`monitor_agent()` が骨格を `AgentPaths` まで通すこと。

    pidfile を骨格どおりの名前で置いたときだけ PIDFILE_BAD にならないことで確認する。
    """
    (tmp_path / "claude-apply-r1.pid").write_text("12345")
    (tmp_path / "claude-apply-r1-result.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )
    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=False),
    ):
        st = monitor_mod.monitor_agent(
            agent="claude", pr=1, timeout=420, stall_timeout=900, poll=1,
            require_result=True, no_early_error=True,
            stem_template="{agent}-apply-r{id}",
        )
    assert st.status == "OK"
    assert st.result_exists is True


# ---------- 2. tmp ディレクトリの解決 ----------

def test_tmp_dir_honors_cross_refactoring_env(monitor_mod, tmp_path, monkeypatch):
    """`CROSS_REFACTORING_TMP_DIR` も一時ディレクトリとして受け付けること。"""
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path / "rf"))
    monkeypatch.setattr(monitor_mod, "_TMP_DIR_OVERRIDE", None, raising=False)
    assert monitor_mod._tmp_dir() == (tmp_path / "rf").resolve()


def test_cross_review_env_wins_over_cross_refactoring(
    monitor_mod, tmp_path, monkeypatch
):
    """両方あるときは cross-review 側を優先し、既存挙動を変えないこと。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path / "cr"))
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path / "rf"))
    monkeypatch.setattr(monitor_mod, "_TMP_DIR_OVERRIDE", None, raising=False)
    assert monitor_mod._tmp_dir() == (tmp_path / "cr").resolve()


def test_tmp_dir_override_wins_over_env(monitor_mod, tmp_path, monkeypatch):
    """`--tmp-dir` 相当の明示指定が env より優先されること。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path / "cr"))
    monkeypatch.setattr(
        monitor_mod, "_TMP_DIR_OVERRIDE", (tmp_path / "explicit").resolve(),
        raising=False,
    )
    try:
        assert monitor_mod._tmp_dir() == (tmp_path / "explicit").resolve()
    finally:
        monkeypatch.setattr(monitor_mod, "_TMP_DIR_OVERRIDE", None, raising=False)


# ---------- 3. ANSI エスケープの除去 ----------

def test_strip_ansi_removes_color_codes(monitor_mod):
    assert monitor_mod._strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert monitor_mod._strip_ansi("plain") == "plain"


def test_early_error_detected_through_ansi_escapes(monitor_mod, tmp_path):
    """色コードが挟まっていても行頭アンカー付きパターンが一致すること。

    kiro は `NO_COLOR=1` / `TERM=dumb` / 非 TTY のいずれでも色コードを出す。
    除去しないと `^Authentication failed` が行頭に来ず取りこぼす。
    """
    log = tmp_path / "err.log"
    log.write_text("\x1b[1;31mAuthentication failed\x1b[0m: token expired\n")
    assert monitor_mod._scan_early_fatal(log) is not None


# ---------- 4. claude / kiro の早期エラー ----------

def test_kiro_tool_rejection_is_fatal(monitor_mod, tmp_path):
    """kiro のツール拒否は終了コード 0 で出るため、標準エラー出力で検知する。"""
    log = tmp_path / "err.log"
    log.write_text(
        "\x1b[33mTool 'execute_bash' is rejected because it matches one or more "
        "rules on the denied list\x1b[0m\n"
    )
    assert monitor_mod._scan_early_fatal(log) is not None


def test_kiro_trust_tools_spelling_warning_is_fatal(monitor_mod, tmp_path):
    """綴り違いで「何も信頼しない状態の正常終了」になる経路を検知する。"""
    log = tmp_path / "err.log"
    log.write_text("WARNING: --trust-tools arg for custom tool 'bogus' ignored\n")
    assert monitor_mod._scan_early_fatal(log) is not None


def test_claude_root_permission_error_is_fatal(monitor_mod, tmp_path):
    log = tmp_path / "err.log"
    log.write_text(
        "--dangerously-skip-permissions cannot be used with root/sudo privileges\n"
    )
    assert monitor_mod._scan_early_fatal(log) is not None


def test_claude_permission_denials_detected_in_stdout_json(monitor_mod, tmp_path):
    """承認失敗は標準出力の JSON に出るため、そちらを見る必要がある。"""
    out = tmp_path / "stdout.log"
    out.write_text(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "permission_denials": [{"tool_name": "Write", "tool_use_id": "x"}],
    }), encoding="utf-8")
    assert monitor_mod._scan_claude_stdout_fatal(out) is not None


def test_claude_is_error_true_detected_in_stdout_json(monitor_mod, tmp_path):
    out = tmp_path / "stdout.log"
    out.write_text(json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "permission_denials": [],
    }), encoding="utf-8")
    assert monitor_mod._scan_claude_stdout_fatal(out) is not None


def test_claude_success_json_is_not_fatal(monitor_mod, tmp_path):
    """正常終了の JSON を致命と誤判定しないこと（空配列 / false）。"""
    out = tmp_path / "stdout.log"
    out.write_text(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "permission_denials": [],
        "modelUsage": {"claude-opus-5": {"inputTokens": 10}},
    }), encoding="utf-8")
    assert monitor_mod._scan_claude_stdout_fatal(out) is None


def test_claude_stdout_scan_ignores_missing_file(monitor_mod, tmp_path):
    assert monitor_mod._scan_claude_stdout_fatal(tmp_path / "nope.log") is None


# ---------- 5. 追加ランタイムの stall 既定 ----------

def test_stall_defaults_cover_claude_and_kiro(monitor_mod, monkeypatch):
    """`claude -p` は完了まで無出力なので、最も長い既定を持つこと。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.delenv("MONITOR_STALL_CLAUDE", raising=False)
    monkeypatch.delenv("MONITOR_STALL_KIRO", raising=False)
    assert monitor_mod._agent_stall_default("claude") == 900
    assert monitor_mod._agent_stall_default("kiro") == 480


# ---------- 6. 移設シム ----------

def test_shim_exposes_implementation_namespace():
    """`scripts/monitor.py` が `lib/monitor.py` の名前空間をそのまま持つこと。

    差し替え可能でなければ既存テストの `mock.patch.object` が届かない。
    """
    here = pathlib.Path(__file__).resolve().parent
    shim = here.parent / "scripts" / "monitor.py"
    impl = here.parent / "scripts" / "lib" / "monitor.py"
    assert shim.is_file() and impl.is_file()

    name = "cross_review_monitor_shim_probe"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, shim)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        assert callable(mod.monitor_agent)
        # 実体側で定義された関数の名前解決先がシムの名前空間であること。
        # そうでないと既存テストの `mock.patch.object(monitor_mod, ...)` が届かない。
        assert mod.monitor_agent.__globals__ is mod.__dict__
        assert mod.monitor_agent.__globals__["_pid_alive"] is mod._pid_alive
    finally:
        sys.modules.pop(name, None)
