"""根拠の行を確かめるための、PR 全体の差分と、変わったファイルの本文の読み取り（git は一時のリポジトリで本物を打つ）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ndf.adapters import git, markdown
from ndf.domains.crossreview import changes, drive
from ndf.domains.crossreview.changes import DiffFile

from ._repos import BRANCH, Repos, commit, count_git, sh
from .test_crossreview_run import Fake, _run

# 囲みの中に根拠の行を持つ文書（囲みの外の行を多くして、書き換えても改名とみなされるようにする）
FILLER = [f"本文 {n}" for n in range(1, 21)]
CITED = "- 根拠: Value 1（MVV バージョン 1）"
FENCED = ["# 設計", "", "```text", CITED, "```", "", *FILLER, CITED, ""]
UNFENCED = [line for line in FENCED if not line.startswith("```")]


@pytest.fixture
def tree(repos: Repos, tmp_path: Path) -> Path:
    """PR のブランチの worktree。"""
    return repos.add_worktree(tmp_path / "tree")


def _bytes(tree: Path, name: str, body: bytes) -> None:
    (tree / name).parent.mkdir(parents=True, exist_ok=True)
    (tree / name).write_bytes(body)


def _commit(tree: Path) -> str:
    sh(tree, "add", "-A")
    sh(tree, "commit", "-q", "--allow-empty", "-m", "変える")
    return sh(tree, "rev-parse", "HEAD").strip()


def _diff(tree: Path, base: str) -> str:
    return changes.review_diff(git.Git(tree), base, "HEAD")


def _files(tree: Path, base: str) -> dict[str, DiffFile]:
    return {f.path: f for f in changes._diff_files(_diff(tree, base))}


# --- 改名したファイルのベースの本文 -----------------------------------------------------------


@pytest.mark.parametrize("new", ["docs/新しい 設計.md", 'docs/"引用".md', "docs/plain.md"])
def test_base_text_of_a_renamed_file_is_read_by_its_old_path(tree: Path, new: str) -> None:
    """改名したうえで囲みを消した文書でも、ベースの本文を改名前のパスで読み、囲みの中にあった行だけを見分けられる。"""
    base = commit(tree, "docs/設計.md", "\n".join(FENCED), "元の文書")
    sh(tree, "mv", "docs/設計.md", new)
    commit(tree, new, "\n".join(UNFENCED), "改名して囲みを消す")
    file = _files(tree, base)[new]
    assert file.base_path == "docs/設計.md"
    assert file.removed == ["```text", "```"] and file.added == {}
    text = changes._committed_texts(git.Git(tree), base, [file.base_path])[file.base_path]
    inside = markdown.code_lines(text)
    cited = [no for no, line in enumerate(UNFENCED, 1) if line == CITED]
    at = [changes._base_lines(file.hunks, no) for no in cited]
    assert at == [4, 27]
    assert [inside[no - 1] for no in at if no is not None] == [True, False]


# --- 差分の形 -------------------------------------------------------------------------------


# 差分の形を変える利用者の設定
USER_SETTINGS = {
    "diff.noprefix": "true",
    "diff.mnemonicPrefix": "true",
    "diff.srcPrefix": "old/",
    "diff.context": "0",
    "diff.interHunkContext": "20",
    "diff.suppressBlankEmpty": "true",
    "diff.relative": "true",
    "diff.renames": "false",
    "diff.renameLimit": "1",
    "diff.submodule": "diff",
    "diff.external": "false",
    "diff.up.textconv": "tr a-z A-Z <",
    "diff.up.command": "false",
    "core.quotePath": "true",
    "color.ui": "always",
}


def _submodule(tree: Path, commit_id: str) -> str:
    """`commit_id` を指すサブモジュール `sub` をコミットする（取り出さない）。"""
    sh(tree, "update-index", "--add", "--cacheinfo", f"160000,{commit_id},sub")
    sh(tree, "commit", "-q", "-m", "サブモジュール")
    return sh(tree, "rev-parse", "HEAD").strip()


def _many_changes(tree: Path) -> str:
    """書き換えて改名した 2 つの文書と、変えた日本語のパスの文書、離れた 2 行を変えた文書、サブモジュールを持つ PR。ベースのコミットを返す。"""
    for name in ("a", "b"):
        _bytes(tree, f"{name}.md", "\n".join(f"{name} の本文 {n}" for n in range(30)).encode())
    _bytes(tree, "日本語.md", b"one\n\ntwo\n")
    _bytes(tree, "far.md", _numbered(*range(1, 31)))
    _commit(tree)
    base = _submodule(tree, "1" * 40)
    for name in ("a", "b"):
        sh(tree, "mv", f"{name}.md", f"{name}{name}.md")
        _bytes(tree, f"{name}{name}.md", "\n".join(f"{name} の本文 {n}" for n in range(1, 30)).encode())
    _bytes(tree, "日本語.md", b"one\n\nthree\n")
    _bytes(tree, "far.md", _numbered(*(f"変えた {n}" if n in (4, 22) else n for n in range(1, 31))))
    _commit(tree)
    _submodule(tree, "2" * 40)
    return base


def test_user_settings_do_not_change_the_diff(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """接頭辞・前後の行数・改名の検出・差分の変換などの利用者の設定のどれか 1 つがあっても、同じ差分が出る。"""
    base = _many_changes(tree)
    plain = _diff(tree, base)
    attributes = Path(sh(tree, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes").strip())
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text("*.md diff=up\n", encoding="utf-8")
    # 設定は環境変数で 1 つずつ置く（履歴と基準の差分を使い回し、設定のたびに git を起動しない）
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    for key, value in USER_SETTINGS.items():
        monkeypatch.setenv("GIT_CONFIG_KEY_0", key)
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", value)
        assert _diff(tree, base) == plain, key
    assert "--- a/日本語.md\n+++ b/日本語.md\n@@ -1,3 +1,3 @@\n one\n \n-two\n+three\n" in plain
    assert plain.count("rename from ") == 2 and "+++ b/sub\n" in plain
    assert "@@ -1,7 +1,7 @@\n" in plain and "@@ -19,7 +19,7 @@" in plain


def test_diff_keeps_carriage_returns_and_survives_other_encodings(tree: Path) -> None:
    """行の途中の CR を改行に変えず（hunk の行数が合わなくなる）、UTF-8 でない本文があっても差分を読める。"""
    base = _commit(tree)
    _bytes(tree, "cr.txt", b"a\rb\r\nc\n")
    _bytes(tree, "sjis.txt", "日本\n後\n".encode("shift_jis"))
    _commit(tree)
    files = _files(tree, base)
    assert files["cr.txt"].added == {1: "a\rb\r", 2: "c"}
    assert list(files["sjis.txt"].added) == [1, 2]


# --- 差分のファイルごとの読み取り ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["日本語.md", 'a"b.md', "a b.md", 'タブ\tと "引用" と 空白.md', "円\\n.md", "bell\a改行\n.md"])
def test_paths_are_read_as_they_are(tree: Path, name: str) -> None:
    """日本語・空白・引用符・タブ・改行・バックスラッシュを含むパスを、足した・変えた文書のどちらでもそのまま読む。"""
    start = _commit(tree)
    _bytes(tree, name, b"x\n")
    base = _commit(tree)
    added = _files(tree, start)[name]
    assert (added.base_path, added.added, added.removed) == (None, {1: "x"}, [])
    _bytes(tree, name, b"y\n")
    _commit(tree)
    changed = _files(tree, base)[name]
    assert (changed.base_path, changed.added, changed.removed) == (name, {1: "y"}, ["x"])


def test_files_without_readable_lines_are_skipped(tree: Path) -> None:
    """消した文書・バイナリ・実行権限だけの変更・空の文書は返さない。中身を変えない改名は、変わった行の無いファイルとして返す。"""
    for name, body in {"gone.md": b"x\n", "bin.dat": b"\0\1", "mode.sh": b"x\n", "moved.md": b"same\n"}.items():
        _bytes(tree, name, body)
    base = _commit(tree)
    sh(tree, "rm", "-q", "gone.md")
    sh(tree, "mv", "moved.md", "after.md")
    _bytes(tree, "bin.dat", b"\0\2")
    _bytes(tree, "empty.md", b"")
    (tree / "mode.sh").chmod(0o755)
    _commit(tree)
    assert list(_files(tree, base).values()) == [DiffFile("after.md", "moved.md", ())]


def test_lines_that_look_like_headers_are_read_as_content(tree: Path) -> None:
    """`---`・`+++`・`@@`・`diff --git` に見える本文の行を、差分の見出しと読み違えない。末尾の改行の有無も行に数えない。"""
    _bytes(tree, "a.md", b"-- a/x\ndiff --git a/x b/x\nsame\nend")
    _bytes(tree, "b.md", b"x\n")
    base = _commit(tree)
    _bytes(tree, "a.md", b"++ b/x\n@@ -1 +1 @@\nsame\nend\nrename to y\n")
    _bytes(tree, "b.md", b"y\n")
    _commit(tree)
    files = _files(tree, base)
    assert list(files) == ["a.md", "b.md"]
    assert files["a.md"].added == {1: "++ b/x", 2: "@@ -1 +1 @@", 4: "end", 5: "rename to y"}
    assert files["a.md"].removed == ["-- a/x", "diff --git a/x b/x", "end"]


# --- head の行からベースの行の番号 -------------------------------------------------------------


def _numbered(*numbers: int | str) -> bytes:
    return "".join(f"{n}\n" for n in numbers).encode()


def test_unchanged_lines_map_to_their_base_numbers(tree: Path) -> None:
    """先頭・途中・末尾の hunk の前・中・後の変わっていない行をベースの番号にし、足した行は None にする。"""
    _bytes(tree, "n.txt", _numbered(*range(1, 41)))
    base = _commit(tree)
    head = ["新", *range(2, 20), "足1", "足2", 20, 21, *range(24, 40), "末尾"]
    _bytes(tree, "n.txt", _numbered(*head))
    _commit(tree)
    hunks = _files(tree, base)["n.txt"].hunks
    assert len(hunks) == 3
    assert [changes._base_lines(hunks, no) for no in range(1, len(head) + 1)] == [n if isinstance(n, int) else None for n in head]


def test_lines_of_a_file_without_hunks_keep_their_numbers() -> None:
    """中身を変えない改名では、行の番号が変わらない。"""
    assert changes._base_lines((), 7) == 7


def test_lines_map_across_hunks_with_no_lines_on_one_side() -> None:
    """片側が 0 行の hunk（消すだけ・足すだけ）でも、前と後ろの行の番号が合う。"""
    text = "diff --git a/n b/n\n--- a/n\n+++ b/n\n@@ -3,2 +2,0 @@\n-c\n-d\n@@ -6,0 +5 @@\n+x\n"
    (file,) = changes._diff_files(text)
    assert [changes._base_lines(file.hunks, no) for no in range(1, 7)] == [1, 2, 5, 6, None, 7]
    assert file.added == {5: "x"}


# --- 置き換えた行の中の変わっていない行 ---------------------------------------------------------


def _hunks(base: str, head: str) -> tuple[changes.Hunk, ...]:
    """`base` の本文全部を `head` に置き換える hunk。"""
    old = [changes.Line(n, None, t) for n, t in enumerate(base.removesuffix("\n").split("\n"), 1)]
    new = [changes.Line(None, n, t) for n, t in enumerate(head.removesuffix("\n").split("\n"), 1)]
    return (changes.Hunk(1, (*old, *new)),)


@pytest.mark.parametrize(
    ("base", "head", "same"),
    [
        # 隣の説明だけを直した（根拠の行は変わっていない）
        (f"古い\r{CITED}\n", f"新しい\r{CITED}\n", {(1, 1): (1, 1)}),
        # 説明を足して、行の中の位置がずれた
        (f"説明\r{CITED}\n", f"説明\r足した\r{CITED}\n", {(1, 0): (1, 0), (1, 2): (1, 1)}),
        # 改行を LF に変えた
        ("a\rb\n", "a\nb\n", {(1, 0): (1, 0), (2, 0): (1, 1)}),
        # 根拠の行を足した・変えた
        ("説明\n", f"説明\r{CITED}\n", {(1, 0): (1, 0)}),
        (f"説明\r{CITED}\n", f"説明\r{CITED}。\n", {(1, 0): (1, 0)}),
    ],
)
def test_unchanged_rows_inside_replaced_lines_are_found(base: str, head: str, same: dict) -> None:
    """単独の CR で区切った 1 行の一部を編集しても、変わっていない Markdown の行をベースの位置に対応させる。"""
    assert changes._same_rows(_hunks(base, head)) == same


def test_rows_are_compared_only_inside_the_same_run_of_replaced_lines() -> None:
    text = "diff --git a/n b/n\n--- a/n\n+++ b/n\n@@ -1,3 +1,3 @@\n-x\ra\n same\n-b\n+c\ra\n+b\n"
    (file,) = changes._diff_files(text)
    assert changes._same_rows(file.hunks) == {(3, 0): (3, 0)}


@pytest.mark.parametrize(("count", "found"), [(50, 50), (200, 0)])
def test_repeated_rows_are_compared_only_up_to_the_limit(count: int, found: int) -> None:
    """前後の行が違う繰り返しの行は、行の組の数の上限までだけ照合し、超えれば対応させない。"""
    rows = [CITED] * count
    assert len(changes._matched(["古い", *rows, "前"], ["新しい", *rows, "後"])) == found


def test_repeated_rows_around_an_edit_are_matched_whatever_their_number() -> None:
    """同じ行が上限を超えて並ぶ中の説明を 1 行変えても、前後の行はすべて対応する。"""
    rows = [CITED] * 5000
    assert sorted(changes._matched([*rows, "古い", *rows], [*rows, "新しい", *rows])) == [(n, n) for n in range(10001) if n != 5000]


def test_rows_that_appear_once_divide_the_comparison() -> None:
    """入れ替えた行は順序を保つ側だけを対応させ、1 回だけ現れる行の間の、繰り返す行も対応させる。"""
    assert sorted(changes._matched(list("a=b=c"), list("a=1=c"))) == [(0, 0), (1, 1), (3, 3), (4, 4)]
    assert sorted(changes._matched(list("abcd"), list("bcad"))) == [(1, 0), (2, 1), (3, 3)]
    assert changes._matched([], list("ab")) == []


# --- コミットの本文 ---------------------------------------------------------------------------


def test_committed_texts_come_from_the_given_commit_in_one_git_call(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """指定したコミットの本文を、何件でも git を 1 回だけ起動し、改行を変えずに読む。シンボリックリンクはたどらない。"""
    bodies = {"a.md": "一\r\n二\r三\n\n", "docs/日本 語.md": "x: y\n", "b.md": "あ\n"}
    for name, body in bodies.items():
        _bytes(tree, name, body.encode())
    _bytes(tree, "empty.md", b"")
    _bytes(tree, "sjis.md", "日\n".encode("shift_jis"))
    (tree / "link.md").symlink_to("a.md")
    old = _commit(tree)
    for name in bodies:
        _bytes(tree, name, b"new\n")
    _commit(tree)
    calls = count_git(monkeypatch)
    got = changes._committed_texts(git.Git(tree), old, [*bodies, "empty.md", "sjis.md", "link.md"])
    assert len(calls) == 1
    assert got.pop("sjis.md").endswith("\n")
    assert got == {**bodies, "empty.md": "", "link.md": ""}


def test_only_empty_files_are_read_as_empty(tree: Path) -> None:
    """本文のある文書が 1 件も無くても失敗にしない。無いコミットは `GitError`。"""
    _bytes(tree, "empty.md", b"")
    _commit(tree)
    assert changes._committed_texts(git.Git(tree), "HEAD", ["empty.md"]) == {"empty.md": ""}
    with pytest.raises(git.GitError):
        changes._committed_texts(git.Git(tree), "0" * 40, ["empty.md"])


@pytest.mark.parametrize(
    "text",
    ["", "\n", "a", "a\n", "a\r\nb\r\n", "a\rb\nc\n", "a\r\r\nb", "a\r", "a\r\r", "\r\n\r", "a b\x0c\x85c\n", "```\rx\n```\n\n    y\n"],
)
def test_lines_are_counted_the_way_the_markdown_parser_counts_them(text: str) -> None:
    """本文の行を Markdown の解析と同じ数え方で並べる（コードの行の番号と、差分の行の番号を対応させる）。"""
    lines = changes._markdown_lines(text)
    assert len(lines) == len(markdown.code_lines(text))
    rows = text.split("\n")
    assert all(line in rows[no - 1] for no, line in lines)


def test_a_line_after_a_carriage_return_keeps_its_line_number_in_the_diff() -> None:
    assert changes._markdown_lines("a\rb\r\nc\n") == [(1, "a"), (1, "b"), (2, "c")]


# --- ラウンドの準備 ---------------------------------------------------------------------------


def test_only_the_round_that_reads_the_whole_diff_keeps_it(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """全差分を読むラウンドだけが全差分のファイルを残し、どのラウンドも head の Markdown の本文を持つ。相手役へ渡す差分は、
    利用者の設定によらない同じ形になる。"""
    sh(wt, "config", "diff.noprefix", "true")
    prepared: list[drive._Prepared] = []
    prepare = drive._prepare_round

    def kept(*args: Any) -> drive._Prepared:
        prepared.append(prepare(*args))
        return prepared[-1]

    monkeypatch.setattr(drive, "_prepare_round", kept)
    fake = Fake(tmp_path / "st", {"codex": [["直す所"], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", "check")
    first, second = prepared
    assert (first.scope.kind, first.full_diff) == ("full", first.diff)
    assert (second.scope.kind, second.full_diff) == ("since", None)
    body = sh(wt, "show", f"origin/{BRANCH}:doc.md")
    assert first.docs == second.docs == {"doc.md": body}
    (file,) = changes._diff_files(first.diff.read_text(encoding="utf-8"))
    assert (file.path, file.base_path) == ("doc.md", "doc.md") and "足した" in file.added.values()
    assert [f.path for f in changes._diff_files(second.diff.read_text(encoding="utf-8"))] == ["fix0.txt"]
