"""agy の hook の経路を検証する（#215 の受け入れ条件 A4〜A6）。

agy は案内を作る時点と渡せる時点が離れている。**tool 実行前の hook がモデルへ文言を返す口
（`reason`）は、拒否のときにしか働かない**（設計の決定 4 の実測）。NDF の誘導は操作を止め
ないため、案内はセッションの控えへ積み、次のモデル呼び出しの前に `PreInvocation` が
`injectSteps[].userMessage` で渡す。

セッション開始時にあたる事象も無いため、モデル呼び出しの通し番号（`invocationNum`）が 0 の
ときを開始時として扱う（決定 5）。

入力の形は agy 1.1.25 で実測したものである。tool の名前は `toolCall.name`、編集先は
`toolCall.args.TargetFile`、作業ディレクトリは `workspacePaths[0]`、セッションの識別子は
`conversationId` にある。**`write_file` は権限の名前であって tool の名前ではない。**
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import GUARD, SCRIPTS_DIR, SESSION, run_lib, write_declaration

HOOKS_JSON = SCRIPTS_DIR.parent / "dev.agy" / "hooks.json"
CONVERSATION = "c-0001"


def run_hook(script: Path, payload: dict, cwd: Path, tmpdir: Path) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["TMPDIR"] = str(tmpdir)
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def edit_call(path: Path, cwd: Path, tool: str = "write_to_file") -> dict:
    """agy の `PreToolUse` の入力。編集先は絶対パスで届く。"""
    return {
        "conversationId": CONVERSATION,
        "stepIdx": 3,
        "workspacePaths": [str(cwd)],
        "modelName": "gemini-3.8-flash-high",
        "toolCall": {"name": tool, "args": {"TargetFile": str(path), "CodeContent": "x"}},
    }


def command_call(command: str, cwd: Path) -> dict:
    """`run_command` は実行ディレクトリを `Cwd` で渡す。"""
    return {
        "conversationId": CONVERSATION,
        "stepIdx": 4,
        "workspacePaths": [str(cwd)],
        "toolCall": {"name": "run_command", "args": {"CommandLine": command, "Cwd": str(cwd)}},
    }


def invocation(cwd: Path, number: int = 1) -> dict:
    """agy の `PreInvocation` の入力。"""
    return {
        "conversationId": CONVERSATION,
        "invocationNum": number,
        "initialNumSteps": 4,
        "workspacePaths": [str(cwd)],
    }


def declared(main_repo: Path) -> Path:
    write_declaration(main_repo, json.dumps({"version": 1}))
    return main_repo


def state_file(tmpdir: Path) -> Path:
    return tmpdir / f"ndf-worktree-{CONVERSATION}.json"


def pending_of(tmpdir: Path) -> list[str]:
    path = state_file(tmpdir)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("pending", [])


def injected(result: dict) -> str:
    """`injectSteps` の `userMessage` を連結して返す。無ければ空文字。"""
    if not result["out"].strip():
        return ""
    steps = json.loads(result["out"]).get("injectSteps", [])
    return "\n".join(step.get("userMessage", "") for step in steps)


# --- A4: 誘導がモデルへ届く -------------------------------------------------


def test_protected_edit_is_queued_and_allowed(main_repo: Path, tmp_path: Path) -> None:
    """主ディレクトリの編集は控えへ積まれ、tool の実行は止まらない。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    target = main_repo / "plugins" / "ndf" / "README.md"

    result = run_hook(GUARD, edit_call(target, main_repo), cwd=main_repo, tmpdir=tmpdir)

    assert result["rc"] == 0, result["err"]
    assert json.loads(result["out"]) == {"decision": "allow"}
    queued = pending_of(tmpdir)
    assert len(queued) == 1
    assert "plugins/ndf/README.md" in queued[0]


def test_pending_reaches_the_next_invocation(main_repo: Path, tmp_path: Path) -> None:
    """積まれた案内は、次のモデル呼び出しの前に `userMessage` として渡される。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    target = main_repo / "plugins" / "ndf" / "README.md"
    run_hook(GUARD, edit_call(target, main_repo), cwd=main_repo, tmpdir=tmpdir)

    result = run_hook(SESSION, invocation(main_repo), cwd=main_repo, tmpdir=tmpdir)

    assert result["rc"] == 0, result["err"]
    assert "plugins/ndf/README.md" in injected(result)


def test_pending_is_delivered_once(main_repo: Path, tmp_path: Path) -> None:
    """取り出した案内は控えから消える。同じ案内を毎回渡さない。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    run_hook(
        GUARD,
        edit_call(main_repo / "plugins" / "ndf" / "README.md", main_repo),
        cwd=main_repo,
        tmpdir=tmpdir,
    )
    run_hook(SESSION, invocation(main_repo), cwd=main_repo, tmpdir=tmpdir)

    assert pending_of(tmpdir) == []
    again = run_hook(SESSION, invocation(main_repo, number=2), cwd=main_repo, tmpdir=tmpdir)
    assert injected(again) == ""


@pytest.mark.parametrize("tool", ["write_to_file", "replace_file_content"])
def test_edit_tools_are_covered(main_repo: Path, tmp_path: Path, tool: str) -> None:
    """編集を伴う tool は 2 つとも誘導の対象になる。

    `write_file` は権限の名前であり、`toolCall.name` には届かない（実測）。
    """
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    target = main_repo / "src" / "app.py"

    run_hook(GUARD, edit_call(target, main_repo, tool=tool), cwd=main_repo, tmpdir=tmpdir)

    assert any("src/app.py" in item for item in pending_of(tmpdir))


def test_shell_write_is_covered(main_repo: Path, tmp_path: Path) -> None:
    """`run_command` は書き込みの形から編集先を推定する。起点は `Cwd` である。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()

    run_hook(GUARD, command_call("echo x > src/app.py", main_repo), cwd=main_repo, tmpdir=tmpdir)

    assert any("src/app.py" in item for item in pending_of(tmpdir))


def test_first_invocation_reports_stray_changes(main_repo: Path, tmp_path: Path) -> None:
    """通し番号が 0 のとき、主ディレクトリの逸脱を提示する（開始時にあたる）。"""
    declared(main_repo)
    (main_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()

    result = run_hook(SESSION, invocation(main_repo, number=0), cwd=main_repo, tmpdir=tmpdir)

    assert "未コミット変更" in injected(result)


def test_later_invocation_does_not_repeat_the_report(main_repo: Path, tmp_path: Path) -> None:
    """通し番号が 0 でないときは逸脱の提示を行わない。毎回の呼び出しで繰り返さない。"""
    declared(main_repo)
    (main_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()

    result = run_hook(SESSION, invocation(main_repo, number=3), cwd=main_repo, tmpdir=tmpdir)

    assert injected(result) == ""
    assert json.loads(result["out"]) == {}


# --- A5: 作業ツリーの中では出さない -----------------------------------------


def test_edit_inside_the_worktree_queues_nothing(
    main_repo: Path, worktree: Path, tmp_path: Path
) -> None:
    """作業ツリーの中の編集は控えへ積まれない。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    target = worktree / "plugins" / "ndf" / "README.md"

    result = run_hook(GUARD, edit_call(target, worktree), cwd=worktree, tmpdir=tmpdir)

    assert result["rc"] == 0, result["err"]
    assert pending_of(tmpdir) == []


def test_allowed_path_queues_nothing(main_repo: Path, tmp_path: Path) -> None:
    """許可されたパスの編集は控えへ積まれない。"""
    declared(main_repo)
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()

    run_hook(GUARD, edit_call(main_repo / "docs" / "note.md", main_repo), cwd=main_repo, tmpdir=tmpdir)

    assert pending_of(tmpdir) == []


# --- A6: 宣言が無いリポジトリでは何もしない ---------------------------------


def test_no_declaration_produces_no_output(main_repo: Path, tmp_path: Path) -> None:
    """`.ndf/worktree.json` が無ければ、どちらの hook も何も出力しない。"""
    tmpdir = tmp_path / "state"
    tmpdir.mkdir()
    target = main_repo / "plugins" / "ndf" / "README.md"

    guard = run_hook(GUARD, edit_call(target, main_repo), cwd=main_repo, tmpdir=tmpdir)
    session = run_hook(SESSION, invocation(main_repo, number=0), cwd=main_repo, tmpdir=tmpdir)

    assert guard["rc"] == 0 and guard["out"].strip() == ""
    assert session["rc"] == 0 and session["out"].strip() == ""


# --- hook の結線 -------------------------------------------------------------


def test_hooks_json_matcher_matches_the_library() -> None:
    """`dev.agy/hooks.json` の `matcher` は共通ライブラリの一覧から作る。

    tool 名の一覧を 2 箇所に持つと、判定を足しても hook が起動しない状態になる。
    """
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    matchers = {
        entry.get("matcher")
        for hook in config.values()
        for entry in hook.get("PreToolUse", [])
    }
    assert run_lib("wt_tool_matcher").stdout.strip() in matchers, matchers


def test_hooks_json_wires_both_scripts() -> None:
    """`PreToolUse` は誘導を、`PreInvocation` は開始時と案内の受け渡しを担う。

    `PreInvocation` は matcher を取らず、handler の配列を直に持つ（agy の書式）。
    """
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    pre_tool = [
        hook["command"]
        for definition in config.values()
        for entry in definition.get("PreToolUse", [])
        for hook in entry["hooks"]
    ]
    pre_invocation = [
        handler["command"]
        for definition in config.values()
        for handler in definition.get("PreInvocation", [])
    ]
    assert any("worktree-guard.sh" in c for c in pre_tool), pre_tool
    assert any("worktree-session.sh" in c for c in pre_invocation), pre_invocation


@pytest.mark.parametrize("tool", ["write_to_file", "replace_file_content"])
def test_agy_tool_names_are_in_the_library(tool: str) -> None:
    """agy の tool 名は共通ライブラリの一覧に載っている。"""
    got = run_lib(f'printf "%s\\n" "$WT_EDIT_TOOLS"').stdout.strip()
    assert tool in got.split("|"), got
