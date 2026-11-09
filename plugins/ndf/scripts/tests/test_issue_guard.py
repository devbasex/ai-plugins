"""起票の guard（hook_lib/issue_guard.py、#2737 の受け入れ条件 11・12 と I6〜I8）。

hook.py の振り分け（`dispatch`）に Claude Code の PreToolUse（Bash）の入力を渡し、拒否の有無と理由を見る。
GitHub は呼ばない。`-R` の判定に使う origin は一時ディレクトリの git リポジトリに置く。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import hook  # noqa: E402

REPO = "o/r"
FEATURE = """## 目的

今は X。Y が困る。Z が成り立つ。

## 対象範囲

含む: A
含まない: B

## 受け入れ条件

- [ ] C になる

## 起票者の推定

無し
"""


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", f"https://github.com/{REPO}.git"], check=True)
    monkeypatch.setenv("NDF_CONTEXT_GUARD", "0")
    monkeypatch.setenv("NDF_SLEEP_GUARD", "0")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def decide(cwd: Path, command: str, runtime: str = "claude"):
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), "session_id": "s"}
    return hook.dispatch("", runtime, raw)


def reason(out) -> str | None:
    spec = (out or {}).get("hookSpecificOutput") or {}
    return spec.get("permissionDecisionReason") if spec.get("permissionDecision") == "deny" else None


def write(cwd: Path, text: str, name: str = "body.md") -> str:
    (cwd / name).write_text(text, encoding="utf-8")
    return name


def test_a_complete_body_file_passes(repo):
    assert reason(decide(repo, f"gh issue create --title t --label enhancement --body-file {write(repo, FEATURE)}")) is None


def test_a_missing_required_item_is_denied_with_the_item_and_the_means(repo):
    body = FEATURE.replace("## 対象範囲\n\n含む: A\n含まない: B\n", "")
    why = reason(decide(repo, f"gh issue create --title t -l enhancement -F {write(repo, body)}"))
    assert why is not None
    assert "対象範囲" in why and "issue-file.py create" in why and "--body-file" in why and "issue-items.json" in why


def test_the_presumption_is_required_for_an_ai_filing(repo):
    body = FEATURE.replace("## 起票者の推定\n\n無し\n", "")
    assert "起票者の推定" in reason(decide(repo, f"gh issue create -t t -l enhancement -F {write(repo, body)}"))


@pytest.mark.parametrize("labels", ["", "--label bug --label enhancement", "--label bug,enhancement", "--label documentation"])
def test_a_body_without_exactly_one_kind_is_denied_as_kind_unknown(repo, labels):
    why = reason(decide(repo, f"gh issue create -t t {labels} -F {write(repo, FEATURE)}"))
    assert why is not None and "種類（不明）" in why


def test_a_body_string_is_read(repo):
    body = FEATURE.replace("'", "")
    assert reason(decide(repo, f"gh issue create -t t --label=enhancement --body '{body}'")) is None
    assert reason(decide(repo, "gh issue create -t t --label enhancement --body '## 目的\n\nx\n'")) is not None


def test_a_quoted_heredoc_body_is_read(repo):
    ok = f"gh issue create -t t -l enhancement --body \"$(cat <<'EOF'\n{FEATURE}EOF\n)\""
    assert reason(decide(repo, ok)) is None
    bad = "gh issue create -t t -l enhancement --body \"$(cat <<'EOF'\n## 目的\n\nx\nEOF\n)\""
    assert "対象範囲" in reason(decide(repo, bad))


@pytest.mark.parametrize(
    "tail",
    ["--body-file -", '--body "$BODY"', "--web", "-F missing.md", "-b"],
)
def test_a_body_in_an_unreadable_form_is_denied(repo, tail):
    why = reason(decide(repo, f"gh issue create -t t -l enhancement {tail}"))
    assert why is not None and "読めない形" in why


def test_short_options_with_the_value_in_the_same_word_pass(repo):
    """gh と同じく `-l値`・`-l=値`・`-F値` を名前と値へ分ける（PR 2856 round 3）。"""
    name = write(repo, FEATURE)
    assert reason(decide(repo, f"gh issue create -t t -lenhancement -F{name}")) is None
    assert reason(decide(repo, f"gh issue create -t t -l=enhancement -F={name}")) is None


def test_another_repository_is_not_judged(repo):
    assert reason(decide(repo, "gh issue create -R other/repo -t t --body-file -")) is None
    assert reason(decide(repo, f"gh issue create --repo {REPO} -t t --body-file -")) is not None


def test_wrappers_and_lists_are_seen(repo):
    name = write(repo, "## 目的\n\nx\n")
    assert reason(decide(repo, f"cd . && env GH_TOKEN=x gh issue create -t t -l enhancement -F {name}")) is not None
    assert reason(decide(repo, f"FOO=1 command gh issue create -t t -l enhancement -F {name} | cat")) is not None


def test_the_body_file_is_read_from_the_directory_after_cd(repo):
    sub = repo / "sub"
    sub.mkdir()
    write(sub, FEATURE)
    assert reason(decide(repo, "cd sub && gh issue create -t t -l enhancement -F body.md")) is None
    assert reason(decide(repo, "(cd sub && gh issue create -t t -l enhancement -F body.md)")) is None
    why = reason(decide(repo, "cd sub; cd ..; gh issue create -t t -l enhancement -F body.md"))
    assert why is not None and "読めない形" in why


@pytest.mark.parametrize(
    "command",
    [
        "printf broken > body.md; gh issue create -t t -l enhancement -F body.md",
        "echo x >> ./body.md && gh issue create -t t -l enhancement -F body.md",
        "cp other.md body.md && gh issue create -t t -l enhancement --body-file body.md",
        "cd sub && printf x > ../body.md; cd ..; gh issue create -t t -l enhancement -F body.md",
        "gh issue create -t t -l enhancement -F body.md > body.md",
    ],
)
def test_a_body_file_written_earlier_in_the_same_command_is_denied(repo, command):
    """実行前の古い本文で通さない（PR 2856 round 4）。"""
    write(repo, FEATURE)
    (repo / "sub").mkdir()
    why = reason(decide(repo, command))
    assert why is not None and "別の Tool 呼び出し" in why


def test_writes_to_other_files_or_after_the_filing_pass(repo):
    write(repo, FEATURE)
    assert reason(decide(repo, "printf x > note.md; gh issue create -t t -l enhancement -F body.md")) is None
    assert reason(decide(repo, "gh issue create -t t -l enhancement -F body.md && printf x > body.md")) is None


@pytest.mark.parametrize("tail", ['--body="$EMPTY"', '--body="## 目的\n\n$X\n"', "--body=$(cat body.md)", '--body-file="$F"'])
def test_an_inline_body_value_with_an_expansion_is_unreadable(repo, tail):
    why = reason(decide(repo, f"gh issue create -t t -l enhancement {tail}"))
    assert why is not None and "読めない形" in why


@pytest.mark.parametrize(
    "command",
    [
        "echo gh issue create",
        "gh issue list --label bug",
        "gh pr create --title t --body x",
        "cat <<'EOF'\ngh issue create -t t\nEOF",
    ],
)
def test_other_commands_pass(repo, command):
    assert reason(decide(repo, command)) is None


def test_other_runtimes_are_not_judged(repo):
    assert reason(decide(repo, "gh issue create -t t --body-file -", runtime="codex")) is None


def test_the_guard_does_not_call_github(repo, monkeypatch):
    """判定は本文とラベルだけで決まり、gh を起動しない（I8）。"""
    import gh_call

    def boom(*a, **k):
        raise AssertionError("gh を呼んだ")

    monkeypatch.setattr(gh_call, "RUNNER", boom)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert reason(decide(repo, f"gh issue create -t t -l enhancement -F {write(repo, '## 目的')}")) is not None


def test_the_hook_entrypoint_denies(repo):
    """hook.py を PreToolUse の入力で起動すると、拒否が 1 行の JSON で出る（配線の確かめ）。"""
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(repo), "session_id": "s"}
    raw["tool_input"] = {"command": "gh issue create -t t --body-file -"}
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "hook.py")], input=json.dumps(raw), capture_output=True, text=True, cwd=repo, timeout=60
    )
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def _body_word_text(arg: str) -> str | None:
    """`gh issue create --body <arg>` の `<arg>` の語を `_heredoc_text` に渡した結果（現状固定）。"""
    from hook_lib import issue_guard as guard
    import shparse as sp

    node = guard.creates(sp.parse_bash(f"gh issue create --body {arg}"))[0]
    words = guard._call_words(node)
    i = [sp.unquote(w) for w in words].index("--body")
    return guard._heredoc_text(words[i + 1])


@pytest.mark.parametrize(
    ("arg", "text"),
    [
        ("\"$(cat <<'EOF'\nhello $X\nEOF\n)\"", "hello $X\n"),
        ('"$(cat <<"EOF"\nhello $X\nEOF\n)"', "hello $X\n"),
        ("$(cat <<'EOF'\nhi\nEOF\n)", "hi\n"),
        ('"$(cat <<EOF\nplain\nEOF\n)"', "plain\n"),
    ],
)
def test_heredoc_text_reads_a_cat_heredoc_in_a_substitution(arg, text):
    assert _body_word_text(arg) == text


@pytest.mark.parametrize(
    "arg",
    [
        '"$(cat <<EOF\na $X\nEOF\n)"',
        '"$(cat <<EOF\na `x`\nEOF\n)"',
        '"$(cat <<EOF\na \\$\nEOF\n)"',
        "\"x$(cat <<'EOF'\nhi\nEOF\n)\"",
        "\"$(cat <<'EOF'\nhi\nEOF\n)$(cat <<'EOF'\nhi\nEOF\n)\"",
        "\"$(printf <<'EOF'\nhi\nEOF\n)\"",
        '"$(cat f.md)"',
        "'plain'",
        "\"$(cat <<'A' <<'B'\na\nA\nb\nB\n)\"",
    ],
)
def test_heredoc_text_is_none_for_other_forms(arg):
    assert _body_word_text(arg) is None


def _create_args(args: str, cwd: Path):
    """`gh issue create <args>` の引数を `create_args` に渡した結果（現状固定）。"""
    from hook_lib import issue_guard as guard
    import shparse as sp

    node = guard.creates(sp.parse_bash(f"gh issue create {args}"))[0]
    words = guard._call_words(node)
    start = guard._start([sp.unquote(w) for w in words]) + 3
    return guard.create_args(words[start:], str(cwd))


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("", (None, [], None)),
        ("--title t", (None, [], None)),
        ("--body x", ("x", [], None)),
        ("-b 'a b'", ("a b", [], None)),
        ("--body=x", ("x", [], None)),
        ("--body=", ("", [], None)),
        ("-b=x", ("x", [], None)),
        ("-bx", ("x", [], None)),
        ("-lenhancement -l=bug,a", (None, ["enhancement", "bug", "a"], None)),
        ("-b$X", (None, [], None)),
        ("--body a --body b", ("b", [], None)),
        ("--label a,b -l ' c , ,d' --label=e", (None, ["a", "b", "c", "d", "e"], None)),
        ("--label ''", (None, [], None)),
        ("--repo o/x", (None, [], "o/x")),
        ("-R=o/x", (None, [], "o/x")),
        ("-Ro/x", (None, [], "o/x")),
        ("--repo=o/x -b y", ("y", [], "o/x")),
        ("--body x --web", (None, [], None)),
        ("-w --body x", (None, [], None)),
        ("--body $X", (None, [], None)),
        ('--body "a $X"', (None, [], None)),
        ("--body x --body $X --body y", (None, [], None)),
        ("--body", (None, [], None)),
        ("--label", (None, [], None)),
        ("--repo", (None, [], None)),
        ("--body \"$(cat <<'EOF'\nhi $X\nEOF\n)\" -l bug", ("hi $X\n", ["bug"], None)),
    ],
)
def test_create_args_reads_body_labels_and_repo(tmp_path, args, expected):
    assert _create_args(args, tmp_path) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("--body-file f.md", "file body\n"),
        ("-F f.md", "file body\n"),
        ("-Ff.md", "file body\n"),
        ("-F=f.md", "file body\n"),
        ("--body-file=f.md", "file body\n"),
        ("--body x --body-file f.md", "file body\n"),
        ("--body-file f.md --body x", "x"),
        ("--body-file missing.md", None),
        ("--body-file missing.md --body x", None),
        ("--body-file -", None),
        ("--body-file=", None),
        ("--body-file bin.md", None),
        ("--body-file", None),
    ],
)
def test_create_args_reads_the_body_file_relative_to_cwd(tmp_path, args, expected):
    (tmp_path / "f.md").write_text("file body\n", encoding="utf-8")
    (tmp_path / "bin.md").write_bytes(b"\xff\xfe\x00")
    assert _create_args(args, tmp_path) == (expected, [], None)


def test_create_args_reads_an_absolute_body_file(tmp_path):
    f = tmp_path / "abs.md"
    f.write_text("abs\n", encoding="utf-8")
    assert _create_args(f"-F {f} -l x", tmp_path / "elsewhere") == ("abs\n", ["x"], None)


# --- issue_decision の分岐の現状固定（cross-refactoring I-002） ---


def _decision(where: Path, command: str, **tool_input):
    from hook_lib import issue_guard as guard
    from hook_lib import payload as pl

    raw = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(where), "session_id": "s"}
    raw["tool_input"] = {"command": command, **tool_input}
    return guard.issue_decision(pl.event_of(raw))


@pytest.mark.parametrize(
    "command",
    ["gh issue list", "gh pr create -t t", "ls issue create", "gh issue create -t t --body-file - &&"],
)
def test_issue_decision_returns_none_without_a_readable_filing(repo, command):
    """語が欠ける・`gh issue create` が無い・構文木を読み切れないときは判定しない。"""
    assert _decision(repo, command) is None


def test_issue_decision_denies_a_gap_with_the_deny_shape(repo):
    out = _decision(repo, f"gh issue create -t t -l enhancement -F {write(repo, '## 目的')}")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "記載項目の定義を満たさない" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_issue_decision_passes_when_the_definition_cannot_be_read(repo, monkeypatch):
    from hook_lib import issue_guard as guard

    def broken(cwd=None):
        raise guard.issue_items.DefinitionError("壊れた定義")

    monkeypatch.setattr(guard.issue_items, "load_definition", broken)
    assert _decision(repo, "gh issue create -t t --body-file -") is None


def test_issue_decision_skips_only_the_filing_whose_definition_cannot_be_read(repo, monkeypatch):
    from hook_lib import issue_guard as guard

    real = guard.issue_items.load_definition
    subprocess.run(["git", "init", "-q", str(repo / "sub")], check=True)  # 定義は作業ツリーの根で読むため、別の木にする

    def per_dir(cwd=None):
        if Path(cwd).name == "sub":
            raise guard.issue_items.DefinitionError("壊れた定義")
        return real(cwd)

    monkeypatch.setattr(guard.issue_items, "load_definition", per_dir)
    assert _decision(repo, "(cd sub && gh issue create -t t --body-file -)") is None
    why = reason(_decision(repo, "(cd sub && gh issue create -t t --body-file -); gh issue create -t t --body-file -"))
    assert why is not None and "読めない形" in why


def test_a_filing_from_a_worktree_subdirectory_reads_that_worktree_definition(repo):
    """サブディレクトリから起票しても、その worktree の `.ndf/issue-items.json` を読む（PR 2861 round 1）。"""
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True
    )
    wt = repo / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    items = json.loads((SCRIPTS / "data" / "issue-items.json").read_text(encoding="utf-8"))
    items["kind_labels"] = {"機能": "type:feature"}
    (wt / ".ndf").mkdir()
    (wt / ".ndf" / "issue-items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    sub = wt / "sub"
    sub.mkdir()
    name = write(sub, FEATURE)
    assert reason(decide(sub, f"gh issue create -t t -l type:feature -F {name}")) is None
    why = reason(decide(sub, f"gh issue create -t t -l enhancement -F {name}"))
    assert why is not None and "type:feature" in why


def test_issue_decision_judges_every_filing_in_the_command(repo):
    """先の起票が通っても、後の起票の欠けで拒否する。"""
    good = write(repo, FEATURE)
    bad = write(repo, "## 目的\n\nx\n", "bad.md")
    assert _decision(repo, f"gh issue create -t t -l enhancement -F {good} && gh issue create -t t -l enhancement -F {good}") is None
    why = reason(_decision(repo, f"gh issue create -t t -l enhancement -F {good}; gh issue create -t t -l enhancement -F {bad}"))
    assert why is not None and "対象範囲" in why


def test_issue_decision_skips_another_repository_and_judges_the_rest(repo):
    assert _decision(repo, "gh issue create -R other/repo -t t --body-file -") is None
    why = reason(_decision(repo, "gh issue create -R other/repo -t t --body-file -; gh issue create -t t --body-file -"))
    assert why is not None and "読めない形" in why


def test_issue_decision_rewritten_body_is_reported_before_the_repository_check(repo):
    """本文のファイルを先に書き込む形は、`-R` が他のリポジトリでも拒否する。"""
    write(repo, FEATURE)
    why = reason(_decision(repo, "printf x > body.md; gh issue create -R other/repo -t t -F body.md"))
    assert why is not None and "別の Tool 呼び出し" in why


def test_issue_decision_reads_the_body_file_from_the_tool_input_directory(repo, tmp_path_factory):
    """Tool の入力にコマンドの実行ディレクトリがあれば、イベントの cwd より優先する。"""
    other = tmp_path_factory.mktemp("other")
    write(other, FEATURE)
    command = "gh issue create -t t -l enhancement -F body.md"
    assert reason(_decision(repo, command)) is not None
    assert _decision(repo, command, cwd=str(other)) is None
