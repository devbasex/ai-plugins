"""lib/monitor.py と分けた 5 本の形（#1142 の C3）。

各モジュールが持つ名前・行数・import の向き（設計の「lib/monitor.py」の節）と、
エントリポイント（`monitor.py` の引数と再エクスポート）が分けた後も変わらないことを固定する。
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
SHIM = LIB.parents[1] / "skills" / "cross-review" / "scripts" / "monitor.py"

HOLDS = {
    "monitor": ["_lib_dir", "_seat_or_both", "main", "_resolve_agents", "_run_all", "_emit_results"],
    "monitor_patterns": [
        "USAGE_LIMIT_FATAL", "EARLY_ERROR_FATAL", "EARLY_ERROR_FATAL_WARNING_SHAPED", "EARLY_ERROR_WARN",
        "EARLY_ERROR_BENIGN", "EARLY_ERROR_BENIGN_KEEP_WARNINGS", "CLI_TIMEOUT_AFTER_EXIT", "CODEX_SENTINEL",
        "ANSI_ESCAPE", "CLAUDE_STDOUT_FATAL", "CLAUDE_STDOUT_USAGE_LIMIT", "_match_is_quoted", "_unescaped_count",
        "_strip_ansi",
    ],
    "monitor_scan": [
        "_read_tail", "_safe_size", "_tail_last_nonempty_line", "_scan_patterns", "_scan_early_fatal",
        "_scan_early_warn", "_scan_claude_stdout", "_scan_claude_stdout_fatal", "_scan_claude_stdout_usage_limit",
        "_scan_codex_sentinel", "EarlyFatal", "_scan_usage_limit", "_scan_fatal", "_early_error",
    ],
    "monitor_proc": [
        "_read_pidfile", "_proc_state", "_pid_alive", "_is_zombie", "_leads_own_group", "_kill_pid",
        "_pid_cmdline_matches",
    ],
    "monitor_types": [
        "DEFAULT_TIMEOUT", "DEFAULT_STALL", "DEFAULT_STALL_AGENT_BUILTIN", "DEFAULT_POLL", "RESULT_AGE_GRACE",
        "DEFAULT_NO_EARLY_ERROR", "_safe_int_env", "_agent_runtime", "_agent_stall_default", "_TMP_DIR_OVERRIDE",
        "TMP_DIR_ENV_VARS", "_tmp_dir", "DEFAULT_STEM_TEMPLATE", "MonitorConfig", "AgentPaths", "MonitorOutcome",
        "AgentStatus",
    ],
    "monitor_loop": [
        "monitor_agent", "_initialize_monitor", "_validate_pid_cmdline", "_update_progress",
        "_lingering_completion", "_timeout_outcome", "_early_error_outcome", "_process_exit_outcome",
        "_stall_outcome", "_finish_monitor", "_emit_progress", "_emit_log",
    ],
    "monitor_outcome": ["_record_outcome"],
}
# 各モジュールが読んでよいライブラリのモジュール（標準ライブラリは数えない）
MAY_IMPORT = {
    "monitor": {"assignment", "limits", "monitor_outcome", "monitor_patterns", "monitor_scan", "monitor_proc",
                "monitor_types", "monitor_loop"},
    "monitor_loop": {"limits", "monitor_outcome", "monitor_patterns", "monitor_scan", "monitor_proc",
                     "monitor_types"},
    "monitor_scan": {"monitor_patterns", "monitor_types"},
    "monitor_proc": set(),
    "monitor_patterns": set(),
    "monitor_types": {"assignment", "limits"},
    "monitor_outcome": {"clock", "monitor_types"},
}
LIB_MODULES = {p.stem for p in LIB.glob("*.py")}


def top_level_names(name: str) -> set[str]:
    tree = ast.parse((LIB / f"{name}.py").read_text(encoding="utf-8"))
    got: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            got.add(node.name)
        elif isinstance(node, ast.Assign):
            got |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            got.add(node.target.id)
    return got


def lib_imports(name: str) -> set[str]:
    tree = ast.parse((LIB / f"{name}.py").read_text(encoding="utf-8"))
    got: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            got |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            got.add(node.module.split(".")[0])
    return got & LIB_MODULES


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", sorted(HOLDS))
def test_each_module_holds_its_names(name):
    assert set(HOLDS[name]) <= top_level_names(name)


def test_moved_names_live_in_one_module_only():
    moved = [n for mod, names in HOLDS.items() if mod != "monitor" for n in names]
    for name in HOLDS:
        if name == "monitor":
            continue
        for n in top_level_names(name) & set(moved):
            assert n in HOLDS[name], f"{n} は {name} にも定義がある"


@pytest.mark.parametrize("name", sorted(HOLDS))
def test_each_module_is_at_most_500_lines(name):
    assert len((LIB / f"{name}.py").read_text(encoding="utf-8").splitlines()) <= 500


@pytest.mark.parametrize("name", sorted(MAY_IMPORT))
def test_imports_point_one_way(name):
    assert lib_imports(name) <= MAY_IMPORT[name]


def test_monitor_reexports_moved_names():
    mod = load(LIB / "monitor.py", "ndf_monitor_layout")
    for src, names in HOLDS.items():
        if src in ("monitor", "monitor_outcome"):
            continue
        other = getattr(mod, src)
        for n in names:
            if n == "_TMP_DIR_OVERRIDE":
                continue
            assert getattr(mod, n) is getattr(other, n), n


def test_shim_sees_the_same_submodules():
    mod = load(SHIM, "ndf_monitor_layout_shim")
    assert mod._run_all.__globals__ is vars(mod)
    assert mod._pid_alive is mod.monitor_proc._pid_alive


def test_entrypoint_help_is_unchanged():
    p = subprocess.run([sys.executable, str(LIB / "monitor.py"), "--help"], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0
    for flag in ["--agents", "--phase", "--timeout", "--stall-timeout", "--no-early-error", "--stem-template",
                 "--tmp-dir"]:
        assert flag in p.stdout
