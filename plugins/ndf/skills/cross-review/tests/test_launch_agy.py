"""共通層が `agy` をどう起動するか（#214）。

委譲先を `gemini` から `agy` へ移した。`agy` は**現在地を作業領域にしない**ため、
渡さないと利用者の見えない場所で作業する。プロンプトも標準入力からは受け取らず、
`-p` の値として取る。この 2 つは起動のコマンド行でしか担保できないので、
組み立てた引数をそのまま固定する。

`agy` そのものは起動しない。PATH へ引数を書き出すだけの実行ファイルを置き、
記録された引数を読む。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

import pytest

LIB = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib"
LAUNCH = LIB / "launch-cli.sh"

# この束（`cross-review/tests`）は外部コマンドを前提にしない一覧に入っている。
# 起動スクリプトを走らせるこのファイルだけが例外なので、前提はここで宣言する。
LAUNCH_COMMANDS = ("bash", "cat", "dirname", "mkdir", "mv", "rm")
pytestmark = pytest.mark.skipif(
    any(shutil.which(c) is None for c in LAUNCH_COMMANDS),
    reason=f"起動スクリプトが使う外部コマンドが無い（{' / '.join(LAUNCH_COMMANDS)}）",
)

# 記録用の実行ファイル。引数を 1 行 1 個で書き出してから終わる。
# 引数は改行を含みうるため、NUL で区切って書き出す。
STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for a in "$@"; do printf '%s\\0' "$a" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""

PROMPT_BODY = "レビューしてください\n2 行目\n"


def _run(tmp_path: pathlib.Path, *extra: str) -> list[str]:
    """共通層に `agy` を起動させ、記録された引数を返す。"""
    workdir = tmp_path / "worktree"
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text(PROMPT_BODY, encoding="utf-8")
    stem = tmp_path / "out" / "agy-review-pr1"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "agy"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)

    args_file = tmp_path / "args.txt"
    subprocess.run(
        [str(LAUNCH), "agy", str(workdir), str(prompt), str(stem), *extra],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NDF_TEST_ARGS_FILE": str(args_file),
        },
        check=True, capture_output=True, text=True,
    )
    # 背景起動なので、書き出しを待つ。
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.05)
    assert args_file.is_file(), "agy が起動されていない"
    return args_file.read_text(encoding="utf-8").split("\0")[:-1]


@pytest.fixture
def args(tmp_path) -> list[str]:
    return _run(tmp_path)


# ---------- 受け入れ条件 3 / 5（作業領域） ----------

def test_the_worktree_is_declared_as_the_workspace(tmp_path) -> None:
    """`--add-dir` に担当の作業ツリーが入る。渡さないと見えない場所で作業する。"""
    got = _run(tmp_path)
    assert "--add-dir" in got
    assert str(tmp_path / "worktree") in got


def test_the_extra_directory_is_added_when_given(tmp_path) -> None:
    """結果ファイルの置き場所が作業ツリーの外にあるときだけ 2 つ目を足す。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    got = _run(tmp_path, "", str(outside))
    assert got.count("--add-dir") == 2
    assert str(outside) in got


def test_only_the_two_declared_directories_are_added(args) -> None:
    """作業領域は 2 つまで。広げるほど、担当ごとに分ける前提が崩れる。"""
    assert args.count("--add-dir") == 1


# ---------- 受け入れ条件 6 / 7 / 8（プロンプトと承認） ----------

def test_the_prompt_is_the_value_of_p(args) -> None:
    """`agy` は標準入力からプロンプトを受け取らない。`-p=<本文>` で渡す。"""
    assert args[-1] == f"-p={PROMPT_BODY.rstrip(chr(10))}"


def test_no_flag_follows_the_prompt(args) -> None:
    """`-p` より後ろにフラグを置くと、それがプロンプトとして読まれる。"""
    assert "-p" not in args, "値を分けて渡すと次の引数がプロンプトとして読まれる"
    assert [a for a in args if a.startswith("-p=")] == [args[-1]]


def test_the_approval_flag_comes_before_the_prompt(args) -> None:
    assert "--dangerously-skip-permissions" in args
    assert args.index("--dangerously-skip-permissions") < len(args) - 1


def test_the_output_format_is_text(args) -> None:
    assert args[args.index("--output-format") + 1] == "text"


# ---------- 受け入れ条件 9（モデル） ----------

def test_the_model_is_passed_when_given(tmp_path) -> None:
    got = _run(tmp_path, "gemini-3-pro")
    assert got[got.index("--model") + 1] == "gemini-3-pro"


def test_the_model_is_absent_when_not_given(args) -> None:
    assert "--model" not in args


# ---------- 受け入れ条件 10（実行時間の上限） ----------

def test_the_print_timeout_is_passed(tmp_path) -> None:
    """CLI 側が先に打ち切ると、結果ファイルが残らない場合と区別が付かない。"""
    got = _run(tmp_path, "", "", "900")
    assert got[got.index("--print-timeout") + 1] == "900s"


def test_the_print_timeout_carries_a_unit(args) -> None:
    """数字だけを渡すと `missing unit in duration` で起動に失敗する（実測）。"""
    assert args[args.index("--print-timeout") + 1].endswith("s")


def test_the_print_timeout_defaults_to_the_longest_phase(args) -> None:
    """渡されなかったときは、いちばん長いフェーズを覆う値を使う。"""
    value = int(args[args.index("--print-timeout") + 1].rstrip("s"))
    assert value >= 3600


# ---------- 受け入れ条件 15（設定ファイルを触らない） ----------

def test_the_settings_file_in_the_worktree_is_left_alone(tmp_path) -> None:
    """起動前の退避も、起動後の復元もしない。"""
    workdir = tmp_path / "worktree"
    workdir.mkdir(parents=True, exist_ok=True)
    settings_dir = workdir / ".agents"
    settings_dir.mkdir()
    settings = settings_dir / "settings.json"
    before = '{"mcpServers": {"x": {"disabled": true}}}'
    settings.write_text(before, encoding="utf-8")

    _run(tmp_path)

    assert settings.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("**/*settings-backup.json"))
    assert not list(tmp_path.glob("**/*settings-sanitized.json"))
