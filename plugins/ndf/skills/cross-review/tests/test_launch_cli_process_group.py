"""起動の手順が CLI を独立したプロセスグループで起動し、監視がグループへ止める（#729 の AC19〜AC21、#584）。

偽の CLI（3 秒後に **子プロセス** が結果ファイルを書く bash）を PATH に `codex` の名前で置き、
`launch-cli.sh` から実際に起動する。監視が pid だけを止めると子が残って結果を書く（#584 の
再現）。グループへ止めれば書かれない。

- AC19: 起動した CLI の pid がプロセスグループの番号（pgid = pid）
- AC20: `_kill_pid` で止めて 4 秒待っても結果ファイルが無い
- AC21: 先頭でない pid では `os.killpg` を呼ばず、pid だけへ送る
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from unittest import mock

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_LAUNCH_CLI = _HERE.parents[2] / "scripts" / "lib" / "launch-cli.sh"

# 標準入力（プロンプト）を読み切ってから、子プロセスが 3 秒後に結果ファイルを書く。
# 親（この script）は `wait` で子を待つので、監視から見て生きている。子を起こした後に
# 目印（`.ready`）を書き、テストは目印を待ってから止める（子が居ない時点で止めると、
# グループで止めなくても結果が書かれず、テストが判別しない）。
FAKE_CLI = """#!/usr/bin/env bash
cat >/dev/null
( sleep 3; echo '{"event": "APPROVE"}' > "$NDF_TEST_RESULT_FILE" ) &
: > "$NDF_TEST_RESULT_FILE.ready"
wait
"""


@pytest.fixture()
def launched(tmp_path):
    """偽の CLI を起動し、(pid, 結果ファイルのパス) を返す。終わりに残ったプロセスを片付ける。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text(FAKE_CLI, encoding="utf-8")
    fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("レビューしてください\n", encoding="utf-8")
    stem = tmp_dir / "codex-review-pr7"
    result = tmp_dir / "codex-review-pr7-result.json"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               NDF_TEST_RESULT_FILE=str(result))

    proc = subprocess.run(
        ["bash", str(_LAUNCH_CLI), "codex", str(work), str(prompt), str(stem)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # 起動の案内の 1 行だけが出る（ジョブ制御の通知が混ざらない）
    assert [l for l in proc.stderr.splitlines() if l.strip()] == [
        l for l in proc.stderr.splitlines() if "launched" in l], proc.stderr
    pid = int((tmp_dir / "codex-review-pr7.pid").read_text().strip())
    for _ in range(500):
        if (tmp_dir / "codex-review-pr7-result.json.ready").exists():
            break
        time.sleep(0.01)
    assert (tmp_dir / "codex-review-pr7-result.json.ready").exists(), "偽の CLI が子を起こしていない"
    try:
        yield pid, result
    finally:
        # **自分のグループへは送らない。** 実装前は CLI がこのテストと同じグループに居るため、
        # 無条件の killpg は pytest 自身を落とす（AC21 が防ぐ事故そのもの）。
        try:
            if os.getpgid(pid) == pid and os.getpgid(pid) != os.getpgrp():
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_launched_cli_leads_its_own_process_group(launched):
    pid, _ = launched
    assert _alive(pid)
    assert os.getpgid(pid) == pid                    # AC19
    assert os.getpgid(pid) != os.getpgrp()           # 起動元（このテスト）のグループではない


def test_killing_the_group_prevents_the_child_from_writing_the_result(launched, monitor_mod):
    pid, result = launched
    assert not result.exists()

    monitor_mod._kill_pid(pid)
    time.sleep(4)

    assert not result.exists()                       # AC20
    assert not _alive(pid) or monitor_mod._is_zombie(pid)


def test_non_leader_pid_is_signalled_alone(monitor_mod):
    """先頭でない pid（pgid ≠ pid）は pid だけへ送り、`os.killpg` を呼ばない（AC21）。"""
    with (
        mock.patch.object(monitor_mod.monitor_proc, "_is_zombie", return_value=False),
        mock.patch.object(monitor_mod.monitor_proc, "_pid_alive", return_value=False),
        mock.patch("os.getpgid", return_value=4242),
        mock.patch("os.killpg") as killpg,
        mock.patch("os.kill") as kill,
    ):
        monitor_mod._kill_pid(4243)
    killpg.assert_not_called()
    kill.assert_called_once_with(4243, signal.SIGTERM)


def test_pid_in_the_monitors_own_group_is_signalled_alone(monitor_mod):
    """pgid = pid でも監視自身のグループなら `os.killpg` を呼ばない（監視まで止まる）。"""
    with (
        mock.patch.object(monitor_mod.monitor_proc, "_is_zombie", return_value=False),
        mock.patch.object(monitor_mod.monitor_proc, "_pid_alive", return_value=False),
        mock.patch("os.getpgid", return_value=4242),
        mock.patch("os.getpgrp", return_value=4242),
        mock.patch("os.killpg") as killpg,
        mock.patch("os.kill") as kill,
    ):
        monitor_mod._kill_pid(4242)
    killpg.assert_not_called()
    kill.assert_called_once_with(4242, signal.SIGTERM)


def test_group_leader_gets_sigterm_then_sigkill_via_killpg(monitor_mod):
    with (
        mock.patch.object(monitor_mod.monitor_proc, "_is_zombie", return_value=False),
        mock.patch.object(monitor_mod.monitor_proc, "_pid_alive", return_value=True),
        mock.patch("os.getpgid", return_value=4242),
        mock.patch("os.getpgrp", return_value=1),
        mock.patch("os.killpg") as killpg,
        mock.patch("os.kill") as kill,
    ):
        monitor_mod._kill_pid(4242, sigterm_grace=0.6)
    assert killpg.call_args_list == [mock.call(4242, signal.SIGTERM), mock.call(4242, signal.SIGKILL)]
    kill.assert_not_called()


def test_getpgid_failure_falls_back_to_the_pid(monitor_mod):
    """`os.getpgid` が失敗する（もう居ない・権限が無い）ときは従来どおり pid だけへ送る。"""
    with (
        mock.patch.object(monitor_mod.monitor_proc, "_is_zombie", return_value=False),
        mock.patch.object(monitor_mod.monitor_proc, "_pid_alive", return_value=False),
        mock.patch("os.getpgid", side_effect=ProcessLookupError),
        mock.patch("os.killpg") as killpg,
        mock.patch("os.kill") as kill,
    ):
        monitor_mod._kill_pid(4242)
    killpg.assert_not_called()
    kill.assert_called_once_with(4242, signal.SIGTERM)
