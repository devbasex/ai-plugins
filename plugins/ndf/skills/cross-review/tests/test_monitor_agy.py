"""監視が委譲先として受け付ける名前と、無くなった早期エラーの判定（#214）。

古い名前を受け付けたままにすると、状態ファイルの鍵と骨格だけが新しくなり、
監視だけが古い一時ファイルを探す。引数の段階で落とす。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

MONITOR = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib" / "monitor.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MONITOR), *args],
        capture_output=True, text=True, timeout=120,
    )


# ---------- 受け入れ条件 18（監視の対象名） ----------

def test_the_monitor_accepts_the_new_name(tmp_path) -> None:
    # 起動待ちの 30 秒を使い切らないよう、終了済みの pid を先に置く。
    (tmp_path / "agy-review-pr1.pid").write_text("2147483646\n", encoding="utf-8")
    r = _run("1", "agy", "--tmp-dir", str(tmp_path), "--timeout", "1", "--poll", "1")
    assert "invalid choice" not in r.stderr
    assert r.returncode != 2


def test_the_monitor_rejects_the_old_name() -> None:
    r = _run("1", "gemini")
    assert r.returncode == 2
    assert "invalid choice" in r.stderr


# ---------- 受け入れ条件 19（無進捗の許容時間） ----------

def test_the_new_name_has_a_stall_default(monitor_mod) -> None:
    assert "agy" in monitor_mod.DEFAULT_STALL_AGENT_BUILTIN
    assert monitor_mod._agent_stall_default("agy") == 480


# ---------- 受け入れ条件 20（無くなった早期エラーの判定） ----------

def test_the_approval_downgrade_is_no_longer_fatal(tmp_path, monitor_mod) -> None:
    """承認モードの降格は、移した先では起きない。判定ごと外す。"""
    log = tmp_path / "err.log"
    log.write_text('Approval mode overridden to "default"\n', encoding="utf-8")
    assert monitor_mod._scan_early_fatal(log) is None


def test_the_configuration_warning_is_no_longer_special_cased(
    tmp_path, monitor_mod
) -> None:
    """設定の検証警告を打ち消す除外も要らなくなる。中断しないことは変わらない。"""
    log = tmp_path / "err.log"
    log.write_text("Error in: mcpServers.serena\n", encoding="utf-8")
    assert monitor_mod._scan_early_fatal(log) is None
