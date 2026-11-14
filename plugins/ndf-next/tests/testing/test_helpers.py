"""揺れない待ち・語の並びの比較・テスト環境の回収の判定・振る舞いの確認の起動。"""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ndf.testing import behavior, cmd_match, testenv, waits


def test_wait_for_file_returns_when_the_file_appears(tmp_path):
    target = tmp_path / "signal"
    threading.Timer(0.05, target.write_text, args=("",)).start()
    assert waits.wait_for_file(target) == target


def test_wait_until_gives_up_only_after_the_timeout():
    with pytest.raises(waits.WaitTimeout):
        waits.wait_until(lambda: False, timeout=0.05)


def test_returns_before_fails_only_when_the_body_waited():
    with waits.returns_before(30):
        pass
    with pytest.raises(AssertionError):
        with waits.returns_before(0):
            pass


def test_same_cmd_ignores_quoting_of_full_width_paths():
    path = "/work/ワーク ツリー/a.py"
    assert cmd_match.same_cmd(f"python3 {shlex.quote(path)} -q", ["python3", path, "-q"])
    assert not cmd_match.same_cmd("python3 a.py", ["python3", "b.py"])
    assert not cmd_match.same_cmd("python3 'a.py", ["python3", "a.py"])


def test_location_seen_climbs_to_the_nearest_surviving_ancestor(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    assert testenv.location_seen(main / ".worktrees" / "gone", main)
    # 親ごと消えた worktree も、残る祖先が同じファイルシステムなら確かめられる
    assert testenv.location_seen(tmp_path / "parent-gone" / "child-gone" / "wt", main)
    # 残る祖先が根だけなら確かめられない
    assert not testenv.location_seen(Path("/ndf-no-such-root-dir") / "wt", main)


def test_owner_of_compares_workdirs_with_row_worktrees():
    assert testenv.owner_of([], ["/w/a"]) == (testenv.NONE, "")
    assert testenv.owner_of(["/w/a", "/w/a/sub"], ["/w/a"]) == (testenv.OWN, "")
    owner, why = testenv.owner_of(["/w/ab"], ["/w/a"])
    assert owner == testenv.FOREIGN and "/w/ab" in why


def test_behavior_check_does_not_inherit_the_callers_stdin(tmp_path, src_env):
    # 呼び出し元の標準入力にほかの依頼文がつながっていても、起動した確認は何も読まない
    caller = (
        "import sys; from pathlib import Path; from ndf.testing import behavior; "
        "argv = [sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))']; "
        "print(behavior.launch(argv, Path('.'), timeout=60).stdout, end='')"
    )
    result = subprocess.run(
        [sys.executable, "-c", caller], input="other prompts\n", cwd=tmp_path, env=src_env, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "''"


def test_behavior_check_command_passes_the_prompt_as_one_argument(tmp_path):
    argv = behavior.claude_check_command(tmp_path, "確かめる 'こと'")
    assert argv[:2] == ["claude", "-p"] and argv[-1] == "確かめる 'こと'"
    assert argv[argv.index("--plugin-dir") + 1] == str(tmp_path)
