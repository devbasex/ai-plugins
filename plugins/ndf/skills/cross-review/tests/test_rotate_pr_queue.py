"""巻き直しの投稿を待ち行列と再実行へ通す（#291、受け入れ条件 4）。

`rotate-pr.sh` が行う投稿は 4 種ある。**扱いは 2 つに分かれる。**

| 投稿 | 上限のときの扱い | なぜ |
| --- | --- | --- |
| `gh pr comment` | 待ち行列へ積み、終了コード 0 で先へ進む | 宛先は決まっており、後から送れる |
| `gh pr close` / `gh pr create` / `gh pr reopen` | 回復を待って再実行する | 作成が終わるまで新しい番号が決まらず、番号が決まらないと以降のすべての項目の宛先が決まらない |

待つあいだラウンドは進まないが、巻き直しは 8 ラウンドに 1 度しか起きない。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROTATE = HERE.parent / "scripts" / "rotate-pr.sh"
QUEUE_PY = HERE.parents[2] / "scripts" / "lib" / "post_queue.py"

# 上限のときに待ち行列か再実行を通さなければならない投稿。
POSTING_COMMANDS = ("gh pr comment", "gh pr close", "gh pr create", "gh pr reopen")


def _code_lines(path: pathlib.Path) -> list[str]:
    """説明文を除いた行。書かれている手順だけを見る。"""
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]


@pytest.mark.parametrize("command", POSTING_COMMANDS, ids=lambda c: c.replace(" ", "-"))
def test_no_posting_command_is_called_bare(command: str) -> None:
    """素の呼び出しが残っていると、その 1 箇所だけが上限で止まる。"""
    bare = [line.strip() for line in _code_lines(ROTATE)
            if command in line and "gh_retry" not in line and "post_pr_comment" not in line]
    assert bare == [], bare


def test_the_posting_helpers_are_defined() -> None:
    body = ROTATE.read_text(encoding="utf-8")
    assert "post_pr_comment()" in body
    assert "gh_retry()" in body


def test_the_shared_layer_is_reached_without_cd() -> None:
    """指し方の契約（`plugins/ndf/scripts/lib/README.md`）に従う。"""
    body = ROTATE.read_text(encoding="utf-8")
    assert "../../../scripts/lib/post_queue.py" in body
    assert 'cd -- "$SCRIPT_DIR/../../../scripts/lib"' not in body
    assert (ROTATE.parent / "../../../scripts/lib/post_queue.py").exists()


# ---- 再実行と積み込みの振る舞い（模した `gh` を通す） ----


def _queue_cli(*args: str, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(QUEUE_PY), *args],
                          capture_output=True, text=True, timeout=120, env=env)


def _env(fake_gh, **over) -> dict:
    import os
    env = {**os.environ, "PATH": f"{fake_gh.dir}{os.pathsep}{os.environ['PATH']}",
           "GH_FAKE_LOG": str(fake_gh.log)}
    env.pop("GH_FAKE_MODE", None)
    env.pop("GH_FAKE_RULES", None)
    env.update(over)
    return env


def test_the_rollback_commands_wait_and_run_again(fake_gh) -> None:
    """上限のあいだ再実行し、回復したら成功する。"""
    fake_gh.set_rules([
        {"match": "", "calls_lt": 3,
         "stdout": '{"message":"API rate limit exceeded.","status":"403"}',
         "stderr": "gh: API rate limit exceeded. (HTTP 403)\n", "exit": 1},
        {"match": "", "stdout": "https://github.com/o/r/pull/9\n"},
    ])
    out = _queue_cli("retry", "--interval", "0.01", "--max-wait", "1",
                     "--", "gh", "pr", "close", "8",
                     env=_env(fake_gh, GH_FAKE_RULES=str(fake_gh.rules_file)))

    assert out.returncode == 0, out.stderr
    assert len(fake_gh.calls()) == 4          # 上限 3 回 + 回復した 1 回
    assert "https://github.com/o/r/pull/9" in out.stdout


def test_the_rollback_commands_do_not_retry_other_failures(fake_gh) -> None:
    """権限の誤りは待っても直らない。1 回で返す。"""
    out = _queue_cli("retry", "--interval", "0.01", "--max-wait", "1",
                     "--", "gh", "pr", "close", "8",
                     env=_env(fake_gh, GH_FAKE_MODE="forbidden"))

    assert out.returncode == 1
    # 403 は上限とも権限の誤りとも読めるため、残り回数を 1 度だけ引いて決める。
    # 対象のコマンドは 1 度しか実行しない。
    assert [c for c in fake_gh.joined() if c.startswith("pr close")] == ["pr close 8"]
    assert any("rate_limit" in c for c in fake_gh.joined())


def test_a_comment_is_queued_instead_of_waiting(fake_gh, tmp_path) -> None:
    """コメントは待たずに積む。待ち行列があれば工程は止まらない。"""
    qdir = tmp_path / "pending"
    body = tmp_path / "body.txt"
    body.write_text("ℹ️ 巻き直しの案内", encoding="utf-8")
    out = _queue_cli("post", "--dir", str(qdir), "--kind", "pr-comment",
                     "--repo", "o/r", "--pr", "8", "--body-file", str(body),
                     "--actor", "takemi",
                     env=_env(fake_gh, GH_FAKE_MODE="rate_limit"))

    assert out.returncode == 0, out.stderr
    assert "QUEUED=1" in out.stdout
    assert len(list(qdir.glob("*.json"))) == 1
