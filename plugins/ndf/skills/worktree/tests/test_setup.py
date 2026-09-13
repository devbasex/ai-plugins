"""宣言ファイルの作成を検証する。

作業ツリー運用の仕組みは、リポジトリ側に宣言ファイルがあるときだけ動く。
このスクリプトだけが、宣言が無い状態で意味を持つ。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from worktree_helpers import SCRIPTS_DIR, git, write_declaration

SETUP = SCRIPTS_DIR / "worktree-setup.sh"


def run(args: list[str], cwd: Path) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        ["bash", str(SETUP), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def declaration(main_repo: Path) -> Path:
    return main_repo / ".ndf" / "worktree.json"


def test_init_creates_a_readable_declaration(main_repo: Path) -> None:
    result = run(["init"], cwd=main_repo)

    assert result["rc"] == 0, result
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert body["version"] == 1
    assert body["$schema"].endswith("worktree.schema.json")


def test_init_makes_the_guard_active(main_repo: Path) -> None:
    """作った直後から、主ディレクトリの編集で案内が出る。"""
    from worktree_helpers import GUARD

    run(["init"], cwd=main_repo)

    payload = {
        "session_id": "setup1",
        "cwd": str(main_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(main_repo / "plugins" / "ndf" / "README.md")},
    }
    proc = subprocess.run(
        ["bash", str(GUARD)], input=json.dumps(payload),
        cwd=str(main_repo), capture_output=True, text=True,
    )
    assert "plugins/ndf/README.md" in proc.stdout, proc.stdout


def test_init_does_not_overwrite(main_repo: Path) -> None:
    """書き加えた内容を消さない。"""
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "guard": {"allow_paths": ["notes/"]}}),
    )

    result = run(["init"], cwd=main_repo)

    assert result["rc"] == 0, result
    assert "既にあります" in result["out"], result["out"]
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert body["guard"]["allow_paths"] == ["notes/"]


def test_force_overwrites(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 1, "guard": {"allow_paths": ["notes/"]}}))

    result = run(["init", "--force"], cwd=main_repo)

    assert result["rc"] == 0, result
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert "guard" not in body


def test_init_runs_from_inside_a_worktree(main_repo: Path, worktree: Path) -> None:
    """作業ツリーの中から呼んでも、主ディレクトリへ置く。"""
    result = run(["init"], cwd=worktree)

    assert result["rc"] == 0, result
    assert declaration(main_repo).exists()
    assert not (worktree / ".ndf" / "worktree.json").exists()


def test_init_outside_a_repository_fails(tmp_path: Path) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    result = run(["init"], cwd=outside)
    assert result["rc"] == 1, result


def test_status_reports_a_missing_declaration(main_repo: Path) -> None:
    result = run(["status"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert "宣言ファイル: なし" in result["out"], result["out"]


def test_status_reports_a_broken_declaration(main_repo: Path) -> None:
    """読めない宣言は「なし」と区別して伝える。気づけないと直せない。"""
    write_declaration(main_repo, "{ not json")
    result = run(["status"], cwd=main_repo)
    assert "読めません" in result["out"], result["out"]


def test_status_counts_worktrees(main_repo: Path, worktree: Path) -> None:
    run(["init"], cwd=main_repo)
    result = run(["status"], cwd=main_repo)
    assert "開発用の作業ツリー: 1 個" in result["out"], result["out"]


def test_status_reports_the_gitignore_registration(main_repo: Path) -> None:
    result = run(["status"], cwd=main_repo)
    assert ".worktrees/ の登録: なし" in result["out"], result["out"]

    (main_repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")

    result = run(["status"], cwd=main_repo)
    assert ".worktrees/ の登録: あり" in result["out"], result["out"]


def test_init_refuses_a_symlinked_declaration(main_repo: Path, tmp_path: Path) -> None:
    """symlink をたどると、リポジトリの外のファイルを書き換えてしまう。"""
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}', encoding="utf-8")
    (main_repo / ".ndf").mkdir()
    (main_repo / ".ndf" / "worktree.json").symlink_to(outside)

    result = run(["init", "--force"], cwd=main_repo)

    assert result["rc"] == 1, result
    assert "symlink" in result["err"], result["err"]
    assert outside.read_text() == '{"keep": true}', "外を書き換えない"


def test_init_refuses_a_symlinked_ndf_directory(main_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (main_repo / ".ndf").symlink_to(outside)

    result = run(["init", "--force"], cwd=main_repo)

    assert result["rc"] == 1, result
    assert not (outside / "worktree.json").exists(), "外へ書かない"


def test_init_leaves_no_temporary_file(main_repo: Path) -> None:
    run(["init"], cwd=main_repo)
    leftovers = [p.name for p in (main_repo / ".ndf").iterdir() if p.name != "worktree.json"]
    assert leftovers == [], leftovers


def test_init_rejects_unknown_argument(main_repo: Path) -> None:
    """現状固定: 未知の引数は 1 で弾かれ、宣言ファイルは作られない。"""
    result = run(["init", "--bogus"], cwd=main_repo)

    assert result["rc"] == 1, result
    assert result["out"] == "", result
    assert result["err"].strip(), result
    assert not declaration(main_repo).exists(), "引数解析で弾かれたときは宣言を作らない"


# --- check: 宣言の状態を終了コードで返す（#527） ------------------------------

MISSING_LINE = "宣言ファイル: なし。`worktree-setup.sh init` で作れます"
BROKEN_LINE = "宣言ファイル: 読めません（版が未対応か、JSON として壊れています）"
PRESENT_LINE = "宣言ファイル: あり（.ndf/worktree.json）"


def first_declaration_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("宣言ファイル:"))


def test_check_reports_a_missing_declaration_without_creating_it(main_repo: Path) -> None:
    """受け入れ条件 1: 無ければ 2 を返し、ファイルを作らない。"""
    result = run(["check"], cwd=main_repo)

    assert result["rc"] == 2, result
    assert result["out"].splitlines()[0] == MISSING_LINE, result["out"]
    assert not (main_repo / ".ndf").exists(), "check は宣言を作らない"


def test_check_reports_a_broken_declaration(main_repo: Path) -> None:
    """受け入れ条件 2: JSON として壊れた宣言は 3。"""
    write_declaration(main_repo, "{ not json")
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 3, result
    assert result["out"].splitlines()[0] == BROKEN_LINE, result["out"]


def test_check_reports_an_unsupported_version(main_repo: Path) -> None:
    """受け入れ条件 2: 未対応の版も読めない側へ分ける。"""
    write_declaration(main_repo, json.dumps({"version": 99}))
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 3, result


def test_check_reports_a_readable_declaration(main_repo: Path) -> None:
    """受け入れ条件 2: init の直後は 0。"""
    run(["init"], cwd=main_repo)
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].splitlines()[0] == PRESENT_LINE, result["out"]


def test_check_outside_a_repository_is_undecidable(tmp_path: Path) -> None:
    """受け入れ条件 2: リポジトリの外は判定できない（1）。理由は標準エラーへ出す。"""
    outside = tmp_path / "plain"
    outside.mkdir()
    result = run(["check"], cwd=outside)
    assert result["rc"] == 1, result
    assert result["out"] == "", result
    assert result["err"].strip(), result


def test_check_runs_from_inside_a_worktree(main_repo: Path, worktree: Path) -> None:
    """作業ツリーの中からでも、主ディレクトリの宣言を見る。"""
    run(["init"], cwd=main_repo)
    result = run(["check"], cwd=worktree)
    assert result["rc"] == 0, result


def test_check_prints_undeclared_branches_as_default(main_repo: Path) -> None:
    """宣言が無いとき、2 行目と 3 行目は既定ブランチへ落ちていることを示す。"""
    result = run(["check"], cwd=main_repo)
    assert result["out"].splitlines()[1:] == [
        "開発の起点: main（未宣言。既定ブランチ）",
        "本番のチャネル: main（未宣言。既定ブランチ）",
    ], result["out"]


def test_check_prints_declared_branches(main_repo: Path) -> None:
    """宣言に書かれた名前は、実在を確かめずにそのまま出す（通信しない）。"""
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "base_branch": "develop", "production_branch": "release"}),
    )
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].splitlines()[1:] == [
        "開発の起点: develop（宣言）",
        "本番のチャネル: release（宣言）",
    ], result["out"]


def test_check_prints_only_base_branch_declared(main_repo: Path) -> None:
    """現状固定: base_branch だけを持つ宣言では、開発の起点は宣言値、本番の
    チャネルは既定ブランチへ落ちる。2 つのキーは独立に判定される。"""
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "base_branch": "develop"}),
    )
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].splitlines()[1:] == [
        "開発の起点: develop（宣言）",
        "本番のチャネル: main（未宣言。既定ブランチ）",
    ], result["out"]


def test_check_prints_only_production_branch_declared(main_repo: Path) -> None:
    """現状固定: production_branch だけを持つ宣言では、両者が逆になる。"""
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "production_branch": "release"}),
    )
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].splitlines()[1:] == [
        "開発の起点: main（未宣言。既定ブランチ）",
        "本番のチャネル: release（宣言）",
    ], result["out"]


def test_check_ignores_branches_in_an_unreadable_declaration(main_repo: Path) -> None:
    """読めない宣言のキーは未宣言として扱う。"""
    write_declaration(main_repo, json.dumps({"version": 99, "base_branch": "develop"}))
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 3, result
    assert result["out"].splitlines()[1] == "開発の起点: main（未宣言。既定ブランチ）", result["out"]


def test_check_reports_an_unknown_default_branch(tmp_path: Path) -> None:
    """origin/HEAD も main / master も無ければ「不明」と出す。"""
    repo = tmp_path / "trunk"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "trunk")
    result = run(["check"], cwd=repo)
    assert result["rc"] == 2, result
    assert result["out"].splitlines()[1:] == [
        "開発の起点: 不明（origin/HEAD が未設定）",
        "本番のチャネル: 不明（origin/HEAD が未設定）",
    ], result["out"]


def test_check_does_not_query_origin(main_repo: Path) -> None:
    """origin が到達できなくても、宣言に書かれた名前をそのまま出して終わる。"""
    git(main_repo, "remote", "add", "origin", "/nonexistent/origin.git")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    result = run(["check"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert "開発の起点: develop（宣言）" in result["out"], result["out"]
    assert "NOTE" not in result["err"], result["err"]


def test_status_and_check_share_the_state(main_repo: Path) -> None:
    """受け入れ条件 3 / 10: status は check と同じ関数から状態を得る。"""
    body = SETUP.read_text(encoding="utf-8")
    status = body[body.index("do_status() {"):]
    status = status[: status.index("\n}\n")]
    assert "wt_declaration_state" in status, "status が状態の関数を呼んでいない"
    assert "wt_declaration " not in status, "status に独自の分岐が残っている"


def _declaration_line_pair(repo: Path) -> tuple[str, str, int]:
    status = run(["status"], cwd=repo)
    check = run(["check"], cwd=repo)
    return first_declaration_line(status["out"]), check["out"].splitlines()[0], check["rc"]


def test_a_directory_declaration_is_unreadable_in_both(main_repo: Path) -> None:
    """`[ -e ]` が真で `[ -f ]` が偽のもの。status と check の 1 行目が一致する。"""
    (main_repo / ".ndf" / "worktree.json").mkdir(parents=True)
    status_line, check_line, rc = _declaration_line_pair(main_repo)
    assert status_line == check_line == BROKEN_LINE
    assert rc == 3


def test_a_dangling_symlink_declaration_is_missing_in_both(main_repo: Path, tmp_path: Path) -> None:
    """壊れた symlink は `[ -e ]` が偽で「なし」。"""
    (main_repo / ".ndf").mkdir()
    (main_repo / ".ndf" / "worktree.json").symlink_to(tmp_path / "gone.json")
    status_line, check_line, rc = _declaration_line_pair(main_repo)
    assert status_line == check_line == MISSING_LINE
    assert rc == 2


def test_status_lines_are_unchanged_for_the_three_states(main_repo: Path) -> None:
    """受け入れ条件 3: 3 つの状態で status の行が変更前と同じ文言である。"""
    assert first_declaration_line(run(["status"], cwd=main_repo)["out"]) == MISSING_LINE
    write_declaration(main_repo, "{ not json")
    assert first_declaration_line(run(["status"], cwd=main_repo)["out"]) == BROKEN_LINE
    run(["init", "--force"], cwd=main_repo)
    assert first_declaration_line(run(["status"], cwd=main_repo)["out"]) == PRESENT_LINE
    assert run(["status"], cwd=main_repo)["rc"] == 0


def test_declaration_state_function(main_repo: Path) -> None:
    """wt_declaration_state は 3 語のいずれかを出し、引数が空なら 1 を返す。"""
    from worktree_helpers import run_lib

    def state() -> str:
        got = run_lib(f'wt_declaration_state "{main_repo}"')
        assert got.returncode == 0, got.stderr
        return got.stdout.strip()

    assert state() == "absent"
    write_declaration(main_repo, "{ not json")
    assert state() == "unreadable"
    write_declaration(main_repo, json.dumps({"version": 1}))
    assert state() == "present"

    empty = run_lib('wt_declaration_state ""')
    assert empty.returncode == 1 and empty.stdout == ""


def test_empty_declaration_file_is_unreadable(main_repo: Path) -> None:
    """現状固定: 0 バイトの宣言ファイルは unreadable と判定する。"""
    from worktree_helpers import run_lib

    declaration(main_repo).parent.mkdir()
    declaration(main_repo).write_bytes(b"")

    result = run_lib(f'wt_declaration_state "{main_repo}"')

    assert result.returncode == 0, result.stderr
    assert result.stdout == "unreadable\n"


def test_hooks_stay_silent_without_a_declaration(main_repo: Path) -> None:
    """受け入れ条件 7: 宣言が無ければ、2 つの hook は何も出さず 0 で終わる。"""
    from worktree_helpers import GUARD, SESSION

    cases = [
        (SESSION, {"session_id": "s527", "cwd": str(main_repo), "hook_event_name": "SessionStart"}),
        (GUARD, {
            "session_id": "s527", "cwd": str(main_repo), "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(main_repo / "src" / "a.py"), "content": "x"},
        }),
    ]
    for script, payload in cases:
        proc = subprocess.run(
            ["bash", str(script)], input=json.dumps(payload),
            cwd=str(main_repo), capture_output=True, text=True,
        )
        assert proc.returncode == 0, (script.name, proc.stderr)
        assert proc.stdout == "", (script.name, proc.stdout)
    assert not (main_repo / ".ndf").exists()
