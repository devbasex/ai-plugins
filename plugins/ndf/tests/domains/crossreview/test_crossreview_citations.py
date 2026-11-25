"""PR が足した MVV の根拠の記述（`- 根拠: Value 5（MVV バージョン 11）`）の確かめと、そのための差分と本文の読み取り
（git は一時のリポジトリで本物を打つ）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from ndf.adapters import git, markdown
from ndf.contracts import result
from ndf.domains.crossreview import changes, drive, fixer, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.changes import DiffFile, Target
from ndf.domains.records import mvv
from ndf.domains.records.mvv_decide import State

from ._repos import BRANCH, Repos, commit, count_git, sh
from .test_crossreview_mvv import MAJOR, _push_base
from .test_crossreview_run import PR, Fake, Recording, _run, _state

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
    lines = changes._markdown_rows(text)
    assert len(lines) == len(markdown.code_lines(text))
    rows = text.split("\n")
    assert all(line in rows[no - 1] for no, line in lines)


def test_a_line_after_a_carriage_return_keeps_its_line_number_in_the_diff() -> None:
    assert changes._markdown_rows("a\rb\r\nc\n") == [(1, "a"), (1, "b"), (2, "c")]


# --- ラウンドの準備 ---------------------------------------------------------------------------


def test_only_the_round_that_reads_the_whole_diff_keeps_it(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """全差分を読むラウンドだけが全差分のファイルを残し、どのラウンドも head の Markdown の本文を持つ。相手役へ渡す差分は、
    利用者の設定によらない同じ形になる。"""
    sh(wt, "config", "diff.noprefix", "true")
    prepared: list[drive._Prepared] = []
    targets: list[Target] = []
    prepare = drive._prepare_round

    def kept(run: Any, round_no: int) -> drive._Prepared:
        prepared.append(prepare(run, round_no))
        targets.append(run.last_target)
        return prepared[-1]

    monkeypatch.setattr(drive, "_prepare_round", kept)
    fake = Fake(tmp_path / "st", {"codex": [["直す所"], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", "check")
    first, second = prepared
    first_target, second_target = targets
    assert (first.scope.kind, first_target.full_diff) == ("full", first.diff)
    assert (second.scope.kind, second_target.full_diff) == ("since", None)
    body = sh(wt, "show", f"origin/{BRANCH}:doc.md")
    assert first_target.docs == second_target.docs == {"doc.md": body}
    (file,) = changes._diff_files(fake.inputs[first.diff.name])
    assert (file.path, file.base_path) == ("doc.md", "doc.md") and "足した" in file.added.values()
    assert [f.path for f in changes._diff_files(fake.inputs[second.diff.name])] == ["fix0.txt"]


# --- 根拠の記述の確かめ（収束ループの中） ----------------------------------------------------------

WRONG = "- 根拠: Value 1（MVV バージョン 1）"
RIGHT = "- 根拠: Value 1（MVV バージョン 11）"


@pytest.fixture(autouse=True)
def _mvv_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))


def _push_pr(repos: Repos, name: str, *lines: str) -> None:
    """PR のブランチへ、`lines` を持つ文書を送る。"""
    sh(repos.other, "fetch", "-q", "origin")
    sh(repos.other, "reset", "-q", "--hard", f"origin/{BRANCH}")
    commit(repos.other, name, "\n".join([*lines, ""]), "文書を変える")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")


class Fixer(Recording):
    """修正のコミットのたびに、`edits` の次の（文書の名前・本文）を同じコミットに入れる偽物。"""

    def __init__(self, state_dir: Path, wt: Path, *edits: Optional[tuple[str, str]], script: Optional[dict[str, list[Any]]] = None):
        super().__init__(state_dir, script if script is not None else {"codex": [], "kiro": []}, decide=lambda fid: self.said)
        self.said: dict[str, str] = {"outcome": fin.FIXED}
        self.wt, self.edits = wt, list(edits)
        self.after_commit = self._edit

    def _edit(self) -> None:
        edit = self.edits.pop(0) if self.edits else None
        if edit is not None:
            (self.wt / edit[0]).write_text(edit[1], encoding="utf-8")
            sh(self.wt, "add", edit[0])
            sh(self.wt, "commit", "-q", "-m", "文書を直す")


def _by_ndf(state_dir: Path) -> list[dict[str, Any]]:
    return [f for f in _state(state_dir)["findings"] if f["by"] == "ndf"]


def _base(capsys, monkeypatch, fake: Fake, wt: Path, *extra: str) -> tuple[int, dict]:
    return _run(capsys, monkeypatch, fake, wt, "--base", "main", *extra)


def test_wrong_version_is_handed_to_the_fixer_until_it_is_corrected(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """承認済みでないバージョンを引いた根拠の記述を足した PR は収束しない。NDF の指摘として修正担当へ渡し、直ると解決する。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    fake = Fixer(tmp_path / "st", wt, ("doc.md", f"# 設計\n\n{RIGHT}\n"))
    code, got = _base(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], got["metrics"]) == (0, "ok", {"rounds": 2, "findings": 1})
    [row] = _by_ndf(fake.state_dir)
    assert (row["id"], row["severity"], row["outcome"]) == ("r1-n1", "major", fin.FIXED)
    assert (row["path"], row["line"], row["cited"], row["unknown"]) == ("doc.md", 2, "Value 1", [])
    assert not {"mvv", "mvv_pinned", "redline"} & set(row)
    [prompt] = [p for agent, _, p in fake.prompts if agent == "claude"]
    assert f"- r1-n1: {row['text']}（major・doc.md:2）" in prompt
    assert "doc.md" in row["text"] and "Value 1" in row["text"] and "バージョン 11" in row["text"]
    assert [r["mvv_checked"] for r in _state(fake.state_dir)["rounds"]] == [True, True]


@pytest.mark.parametrize("said", [fin.FIXED, fin.SKIPPED])
def test_error_left_by_the_fixer_stops_whatever_it_answered(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, said: str) -> None:
    """修正担当が「直した」「見送った」と回答しても、根拠の記述が直っていなければ毎ラウンド同じ指摘として残り（相手役へ渡す差分が
    狭まったラウンドでも PR 全体を確かめる）、上限で止まる。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    fake = Fixer(tmp_path / "st", wt)
    fake.said = {"outcome": said, "reason": "反しない"}
    code, got = _base(capsys, monkeypatch, fake, wt, "--max-rounds", "2")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "mvv_unresolved")
    assert result.code_for(got) == result.code_for({**got, "cause": {**got["cause"], "kind": "work_stopped"}}) == code
    assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == [(fin.GAP_MVV, "r1-n1")]
    assert "r1-n1" in got["summary"]
    rounds = [(r["scope"], r["verdict"]) for r in _state(fake.state_dir)["rounds"]]
    assert rounds == [("full", drive.CHANGES_REQUIRED), ("since", drive.CHANGES_REQUIRED)]
    [row] = _by_ndf(fake.state_dir)
    assert (row["outcome"], row["fixer_reason"]) == (fin.UNRESOLVED, "反しない")
    assert [len(prompt.split("- r1-n1: ")) for agent, _, prompt in fake.prompts if agent == "claude"] == [2, 2]


def _git_fails(monkeypatch: pytest.MonkeyPatch, *names: str) -> list[str]:
    """`changes` の読み取りの関数 `names` を、打つたびに Git の失敗にする。打った名前の並びを返す。"""
    called: list[str] = []

    def failing(name: str) -> Any:
        def fail(*args: Any) -> Any:
            called.append(name)
            raise git.GitError("読めない")

        return fail

    for name in names:
        monkeypatch.setattr(changes, name, failing(name))
    return called


def test_issue_4090_4_failed_check_stops_without_any_finding(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """NDF の指摘が 1 件も無くても、最後のコミットで確かめられなければ止まる。打ち直しはそのラウンドの収束を使わない。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", RIGHT)
    fake = Fixer(tmp_path / "st", wt)
    with monkeypatch.context() as broken:
        _git_fails(broken, "_diff_files")
        _, got = _base(capsys, broken, fake, wt)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "mvv_unresolved")
    assert [g["kind"] for g in got["outputs"]["gaps"]] == [fin.GAP_MVV] and _by_ndf(fake.state_dir) == []
    assert [w for w in got["warnings"] if "根拠の記述" in w and "読めない" in w]
    [record] = _state(fake.state_dir)["rounds"]
    assert (record["verdict"], record["mvv_checked"]) == (fin.CONVERGED, False)
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["mvv_checked"] is False
    again = Fixer(tmp_path / "st", wt)
    code, got = _base(capsys, monkeypatch, again, wt)
    assert (code, got["status"], sorted(again.calls)) == (0, "ok", ["codex", "kiro"])
    assert [(r["scope"], r["mvv_checked"]) for r in _state(again.state_dir)["rounds"]] == [("full", False), ("full", True)]


def test_issue_4090_4_failed_round_is_checked_again_before_the_verdict(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """ラウンドの中の確かめに失敗しても、終わり方を決める前の確かめ直しで誤りを拾い、状態ファイルと PR のスレッドに残す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    read = changes._diff_files
    failing = _git_fails(monkeypatch, "_diff_files")
    reviewed = drive._review_round

    def review_then_recover(*args: Any) -> Any:
        monkeypatch.setattr(changes, "_diff_files", read)
        return reviewed(*args)

    monkeypatch.setattr(drive, "_review_round", review_then_recover)
    fake = Fixer(tmp_path / "st", wt)
    _, got = _base(capsys, monkeypatch, fake, wt)
    assert (got["cause"]["kind"], len(failing)) == ("mvv_unresolved", 1)
    [row] = _by_ndf(fake.state_dir)
    assert (row["id"], row["round"], row["outcome"]) == ("r1-n1", 1, fin.UNRESOLVED)
    assert _state(fake.state_dir)["threads"]["r1-n1"]["comment"]


def test_round_checked_only_at_the_end_is_reused_on_the_same_head(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """ラウンドの中の確かめに失敗しても、同じ head の最後の確かめに成功して収束したら、打ち直しは相手役を起動せずに前回の結果を返す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", RIGHT)
    read, reviewed = changes._diff_files, drive._review_round
    _git_fails(monkeypatch, "_diff_files")

    def review_then_recover(*args: Any) -> Any:
        monkeypatch.setattr(changes, "_diff_files", read)
        return reviewed(*args)

    monkeypatch.setattr(drive, "_review_round", review_then_recover)
    code, got = _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt)
    assert (code, got["status"]) == (0, "ok")
    assert [r["mvv_checked"] for r in _state(tmp_path / "st")["rounds"]] == [True]
    again = Fixer(tmp_path / "st", wt)
    code, got = _base(capsys, monkeypatch, again, wt)
    assert (code, got["status"], again.calls) == (0, "ok", [])


def test_issue_4090_4_last_fix_is_checked_on_the_new_commit(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """最後のラウンドの修正で足した誤りは、終わり方を決める前に新しいコミットで拾い、止まる。同じ状態ディレクトリの
    打ち直しは、その指摘を最初のラウンドから修正担当へ渡す。"""
    _push_base(repos)
    fake = Fixer(tmp_path / "st", wt, ("new.md", f"# 新しい\n\n{WRONG}\n"), script={"codex": [[MAJOR]], "kiro": [[]]})
    _, got = _base(capsys, monkeypatch, fake, wt, "--max-rounds", "1")
    assert got["cause"]["kind"] == "mvv_unresolved"
    [row] = _by_ndf(fake.state_dir)
    assert (row["id"], row["path"], row["outcome"]) == ("r2-n1", "new.md", fin.UNRESOLVED)
    # ラウンドの記録に無いコミットの行の番号なので、スレッドは次の起動が作る
    assert "r2-n1" not in _state(fake.state_dir)["threads"]
    again = Fixer(tmp_path / "st", wt, ("new.md", f"# 新しい\n\n{RIGHT}\n"))
    code, got = _base(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "- r2-n1: " in next(p for agent, _, p in again.prompts if agent == "claude")
    assert [(f["id"], f["outcome"]) for f in _by_ndf(again.state_dir)] == [("r2-n1", fin.FIXED)]
    assert _state(again.state_dir)["threads"]["r2-n1"]["resolved"]


@pytest.mark.parametrize("broken", [False, True])
def test_last_fix_is_settled_only_by_the_check_of_its_commit(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, broken: bool
) -> None:
    """最後のラウンドの修正で直った誤りは、新しいコミットの確かめで解決する。ベースとの共通の祖先を求められず確かめられなければ、
    例外を外へ出さず、未解決のまま残して止まる。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    fake = Fixer(tmp_path / "st", wt, ("doc.md", f"# 設計\n{RIGHT}\n"))
    if broken:
        _git_fails(monkeypatch, "_final_target")
    _, got = _base(capsys, monkeypatch, fake, wt, "--max-rounds", "1")
    left = [(fin.GAP_MVV, "r1-n1"), (fin.GAP_MVV, fin.CITATION_CHECK)] if broken else []
    assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == left
    assert got["status"] == ("stopped" if broken else "ok")
    assert [f["outcome"] for f in _by_ndf(fake.state_dir)] == [fin.UNRESOLVED if broken else fin.FIXED]


def _refuse_push(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fixer.push, "push_fix", lambda *a: fixer.push.PushResult(False, False, None, None, "断られた", ()))


@pytest.mark.parametrize("readable", [True, False])
def test_failed_push_keeps_the_local_errors_out_of_the_pr(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub, readable: bool
) -> None:
    """修正を送れなかったときは、手元に残ったコミットの誤り（確かめられなければその原因）で止まるが、未公開の文書の名前と本文は手元の
    状態ファイルだけに残し、指摘・PR の記録・結果 JSON に載せない。"""
    _push_base(repos)
    fake = Fixer(tmp_path / "st", wt, ("new.md", f"# 新しい\n{WRONG}\n"), script={"codex": [[MAJOR]], "kiro": [[]]})
    _refuse_push(monkeypatch)
    if not readable:

        def fail(*args: Any) -> Any:
            raise git.GitError("new.md を読めない")

        monkeypatch.setattr(changes, "_final_target", lambda run, head: run.last_target if head == run.last_target.head else fail())
    _, got = _base(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "mvv_unresolved" and _by_ndf(fake.state_dir) == []
    names = [g["name"] for g in got["outputs"]["gaps"] if g["kind"] == fin.GAP_MVV]
    assert names == [fin.UNSENT_CITATIONS if readable else fin.CITATION_CHECK]
    held = _state(fake.state_dir)["unsent_citations"]
    assert held["head"] == sh(wt, "rev-parse", "HEAD").strip()
    assert [e["path"] for e in held["errors"]] == ["new.md"] if readable else held["errors"] is None and "new.md" in held["detail"][0]
    assert "new.md" not in json.dumps(got, ensure_ascii=False) and not [c for c in hub.comments if "new.md" in c[2]]
    finished = fin.finish(fin.as_review(store.read_object(fake.state_dir / drive.SUMMARY_NAME)))
    assert finished.kind == "mvv_unresolved"


def test_commit_left_by_a_fixer_without_a_result_stays_out_of_the_pr(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """修正の記録の無い未送信のコミット（修正担当がコミットだけを残して終わった）の誤りは、ラウンドの始めにも指摘にせず、手元の状態
    ファイルだけに残して止まる。打ち直しはその収束を使い回さず、送れば普通の指摘になる。"""
    _push_base(repos)
    commit(wt, "new.md", f"# 新しい\n{WRONG}\n", "送られなかった修正")
    state_dir = tmp_path / "st"
    for _ in range(2):
        again = Fixer(state_dir, wt)
        _, got = _base(capsys, monkeypatch, again, wt)
        assert got["cause"]["kind"] == "mvv_unresolved" and sorted(again.calls) == ["codex", "kiro"]
        assert [g["name"] for g in got["outputs"]["gaps"]] == [fin.UNSENT_CITATIONS]
        assert _by_ndf(state_dir) == [] and _state(state_dir)["rounds"][-1]["mvv_checked"] is False
        assert [e["path"] for e in _state(state_dir)["unsent_citations"]["errors"]] == ["new.md"]
        assert "new.md" not in json.dumps(got, ensure_ascii=False) and not [c for c in hub.comments if "new.md" in c[2]]
    sh(wt, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    code, got = _base(capsys, monkeypatch, Fixer(state_dir, wt, ("new.md", f"{RIGHT}\n")), wt)
    assert (code, got["status"], [f["path"] for f in _by_ndf(state_dir)]) == (0, "ok", ["new.md"])


def test_failed_push_does_not_settle_the_error_left_in_the_pr(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """手元のコミットで誤りが直っていても、送れなければ PR の誤りは未解決のまま止まる。次の起動が送り直すと解決し、手元の記録も消える。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    with monkeypatch.context() as refused:
        _refuse_push(refused)
        _, got = _base(capsys, refused, Fixer(tmp_path / "st", wt, ("doc.md", f"# 設計\n{RIGHT}\n")), wt)
    assert got["cause"]["kind"] == "mvv_unresolved"
    [gap] = got["outputs"]["gaps"]
    assert (gap["kind"], gap["name"]) == (fin.GAP_MVV, "r1-n1")
    assert "doc.md" not in gap["reason"] and "Value 1" not in gap["reason"]
    assert [f["outcome"] for f in _by_ndf(tmp_path / "st")] == [fin.UNRESOLVED]
    code, got = _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt)
    assert (code, got["status"]) == (0, "ok")
    assert [f["outcome"] for f in _by_ndf(tmp_path / "st")] == [fin.FIXED]
    assert "unsent_citations" not in _state(tmp_path / "st")


def test_issue_4090_4_converged_review_is_reused_without_checking_again(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """確かめに成功して収束した head の打ち直しは、確かめ直さずに前回の収束を返す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", RIGHT)
    _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt)
    called = _git_fails(monkeypatch, "_diff_files", "_final_target")
    again = Fixer(tmp_path / "st", wt)
    code, got = _base(capsys, monkeypatch, again, wt)
    assert (code, got["status"], again.calls, called) == (0, "ok", [], [])


def test_issue_4090_12_resolved_thread_does_not_settle_the_error(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """根拠の記述の誤りのスレッドを別の実行か人が resolve しても、直すまで未解決のまま修正担当へ渡す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt, "--max-rounds", "1")
    hub.port.resolved.append(_state(tmp_path / "st")["threads"]["r1-n1"]["comment"])
    again = Fixer(tmp_path / "st", wt)
    again._fixes = 1
    with monkeypatch.context() as broken:
        _git_fails(broken, "_diff_files")
        _base(capsys, broken, again, wt, "--max-rounds", "1")
    assert [f["outcome"] for f in _by_ndf(again.state_dir)] == [fin.UNRESOLVED]
    assert "- r1-n1: " in next(p for agent, _, p in again.prompts if agent == "claude")


def test_error_added_back_by_the_last_fix_leaves_its_resolved_thread(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """直ってスレッドも閉じた誤りを最後の修正が足し直したら、未解決へ戻し、閉じたスレッドから切り離す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt, ("doc.md", f"{RIGHT}\n")), wt)
    assert _state(tmp_path / "st")["threads"]["r1-n1"]["resolved"]
    _push_pr(repos, "doc.md", "# 設計", RIGHT)
    again = Fixer(tmp_path / "st", wt, ("doc.md", f"{WRONG}\n"), script={"codex": [[MAJOR]], "kiro": [[]]})
    _base(capsys, monkeypatch, again, wt, "--max-rounds", "1")
    state = _state(tmp_path / "st")
    assert [f["outcome"] for f in _by_ndf(tmp_path / "st")] == [fin.UNRESOLVED] and "r1-n1" not in state["threads"]


def test_without_an_approved_mvv_nothing_is_checked(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """承認済みの MVV が無ければ確かめず（最後の修正の後も）、前の起動が残した NDF の指摘は解決にする。"""
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    held = {"id": "r1-n1", "key": "k", "text": "誤り", "by": "ndf", "round": 0, "outcome": fin.UNRESOLVED, "severity": "major"}
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    store.write_object(store.state_pr(state_dir, PR).path, {"pr": PR, "findings": [held]})
    called = _git_fails(monkeypatch, "_diff_files", "_final_target")
    fake = Fixer(state_dir, wt, script={"codex": [[MAJOR]], "kiro": [[]]})
    code, _ = _base(capsys, monkeypatch, fake, wt, "--max-rounds", "1")
    assert (code, called, [f["outcome"] for f in _by_ndf(state_dir)]) == (0, [], [fin.FIXED])
    assert _state(state_dir)["rounds"][0]["mvv_checked"] is True


def test_state_is_not_saved_again_when_the_last_check_changes_nothing(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """最後のラウンドと同じ head の確かめ直しは、Git を打たず、状態ファイルも書き直さない。

    確かめ直しの後の保存は、MVV の記録の側（直した指摘を保留と照らし終えた時刻。#3959）のもので、ここでは見ない。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    order: list[Any] = []
    save, check = drive._save, drive._check_final_head

    def checked(*args: Any) -> Any:
        calls = count_git(monkeypatch)
        order.append("check")
        got = check(*args)
        order.append(list(calls))
        return got

    monkeypatch.setattr(drive, "_save", lambda run: order.append("save") or save(run))
    monkeypatch.setattr(drive, "_check_final_head", checked)
    _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt, ("doc.md", f"# 設計\n{RIGHT}\n")), wt)
    last = len(order) - 1 - order[::-1].index("check")
    assert order[last - 1 : last + 2] == ["save", "check", []]


# --- 確かめる行と git の起動の数 ----------------------------------------------------------------


def _snapshot(version: int = 11, items: tuple[str, ...] = ("Value 1", "Value 2", "P1"), state: str = State.APPROVED) -> mvv.Snapshot:
    return mvv.Snapshot(state, version, "a" * 64, None, items, {}, "c" * 40, "b" * 64, None)


@dataclass
class Run:
    """確かめが読み書きする、1 回の起動の値。"""

    g: git.Git
    base: str
    snapshot: Optional[mvv.Snapshot] = field(default_factory=_snapshot)
    warnings: list[str] = field(default_factory=list)
    checked_head: Optional[str] = None
    last_target: Optional[Target] = None
    citations: dict[tuple[str, str, str], list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def cfg(self) -> Any:
        return SimpleNamespace(base_commit=self.base)


def _target(tree: Path, base: str, docs: bool = True) -> Target:
    """`base` から今の head まで。`docs` が真なら、Markdown の本文を読み終えている。"""
    g = git.Git(tree)
    return Target(base, g.rev_parse("HEAD"), changes.changed(g, base, tree).docs if docs else {}, None)


def _full(target: Target, tree: Path, tmp_path: Path) -> Target:
    """全差分のファイルを書き終えた対象。"""
    path = tmp_path / "round.diff"
    path.write_text(changes.review_diff(git.Git(tree), target.merge_base, target.head), encoding="utf-8")
    return target._replace(full_diff=path)


def _found(tree: Path, base: str, **how: Any) -> list[tuple[str, int, str, str]]:
    errors = changes.citations(Run(git.Git(tree), base), _target(tree, base, **how))
    assert errors is not None
    return [(e["path"], e["line"], e["cited"], e["kind"]) for e in errors]


@pytest.mark.parametrize("docs", [True, False])
@pytest.mark.parametrize("name", ["docs/日本語 の設計.md", 'a"b.md', "plain.md"])
def test_added_errors_are_found_wherever_they_are(tree: Path, name: str, docs: bool) -> None:
    """足した行の誤りだけを、種類と Git の行の番号とともに拾う。前からある行・コードブロックの中・文中の引用は拾わない。"""
    base = commit(tree, name, "\n".join(["# 設計", "", WRONG, "", "本文", ""]), "前からある誤り")
    added = [RIGHT, "- 根拠: Value 9（MVV バージョン 11）", "", "```", WRONG, "```", "", "文中の「根拠: Value 1（MVV バージョン 1）」", ""]
    added += ["### 決定 1: 題", "", "- 根拠: Value 2", "", "## 次", "", "- 根拠: 実測", "", "  - 根拠: P1（MVV バージョン 12）"]
    commit(tree, name, "\n".join(["# 設計", "", WRONG, "", "本文", *added, ""]), "足す")
    assert _found(tree, base, docs=docs) == [
        (name, 7, "Value 9", "unknown"),
        (name, 17, "Value 2", "format"),
        (name, 23, "P1", "version"),
    ]


@pytest.mark.parametrize("new", ["docs/設計.md", "docs/新しい 設計.md"])
def test_issue_4099_1_lines_that_came_out_of_a_code_block_are_checked(tree: Path, new: str) -> None:
    """コードブロックの囲みを消しただけで本文に出た根拠の記述を拾う（改名した文書でも、差分の形を変える利用者の設定が
    あっても）。ベースでも本文だった行は拾わない。"""
    sh(tree, "config", "diff.noprefix", "true")
    sh(tree, "config", "diff.context", "0")
    base = commit(tree, "docs/設計.md", "\n".join(FENCED), "元の文書")
    if new != "docs/設計.md":
        sh(tree, "mv", "docs/設計.md", new)
    commit(tree, new, "\n".join(UNFENCED), "囲みを消す")
    assert _found(tree, base) == [(new, 3, "Value 1", "version")]


@pytest.mark.parametrize(("before", "after"), [("- ~~~", "- 例"), ("  ~~~", "  例"), ("1. ```", "1. 例")])
def test_fences_inside_lists_are_noticed(tree: Path, before: str, after: str) -> None:
    """リストの中の囲みの始まりだけを変えたときも、本文に出た行を拾う。"""
    base = commit(tree, "a.md", f"{before}\n   {WRONG}\n", "元の文書")
    commit(tree, "a.md", f"{after}\n   {WRONG}\n", "囲みを変える")
    assert _found(tree, base) == [("a.md", 2, "Value 1", "version")]


def test_lines_split_by_a_carriage_return_are_checked_one_by_one(tree: Path) -> None:
    """`\\r` だけの改行を含む行も Markdown の行ごとに確かめ、ベースとも行ごとに突き合わせる。"""
    _bytes(tree, "a.md", f"```\n前\r{WRONG}\n```\n{WRONG}\r後\n".encode())
    base = _commit(tree)
    _bytes(tree, "a.md", f"前\r{WRONG}\n{WRONG}\r後\n新\r{WRONG}\n".encode())
    _commit(tree)
    assert _found(tree, base) == [("a.md", 1, "Value 1", "version"), ("a.md", 3, "Value 1", "version")]
    _bytes(tree, "b.md", f"文\r```\r{WRONG}\n".encode())
    base = _commit(tree)
    _bytes(tree, "b.md", f"```\n文\r```\r{WRONG}\n".encode())
    _commit(tree)
    assert _found(tree, base) == [("b.md", 2, "Value 1", "version")]


def _edited(tree: Path, count: int, *, fences: bool = False, other: bool = False) -> str:
    """根拠の記述を残した `count` 件の文書を編集する PR。`fences` は囲みも消し、`other` は Markdown でないファイルにも足す。"""
    for n in range(count):
        commit(tree, f"d{n}.md", "\n".join(FENCED), "元の文書")
    base = commit(tree, "note.txt", "x\n", "元のファイル")
    for n in range(count):
        commit(tree, f"d{n}.md", "\n".join([*(UNFENCED if fences else FENCED), "足した", RIGHT, ""]), "編集")
    if other:
        commit(tree, "note.txt", f"x\n{RIGHT}\n", "編集")
    return base


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize(
    ("how", "full", "starts"),
    [
        # 全差分を読んだラウンドで、囲みに触れない編集だけ
        ({}, True, 0),
        # 囲みを消した文書があれば、ベースの本文を 1 回でまとめて読む
        ({"fences": True}, True, 1),
        # Markdown でないファイルに足した行があれば、head の本文を 1 回でまとめて読む
        ({"other": True}, True, 1),
        # 差分が狭まったラウンドは、全差分を 1 回読む
        ({}, False, 1),
        ({"fences": True, "other": True}, False, 3),
    ],
)
def test_issue_4090_3_git_runs_do_not_grow_with_the_documents(
    tree: Path, tmp_path: Path, monkeypatch, count: int, how: dict[str, bool], full: bool, starts: int
) -> None:
    base = _edited(tree, count, **how)
    target = _target(tree, base)
    if full:
        target = _full(target, tree, tmp_path)
    run = Run(git.Git(tree), base)
    calls = count_git(monkeypatch)
    first = changes.citations(run, target)
    assert (len(calls), run.checked_head) == (starts, target.head)
    # 同じ対象の 2 回目と、同じ head の終わり方の判定の前は、読み直さずに同じ結果を返す
    run.last_target = target
    assert changes.citations(run, target) == changes.final_citations(run, target.head) == first
    assert len(calls) == starts


@pytest.mark.parametrize("count", [1, 3])
def test_issue_4090_3_new_commit_at_the_end_takes_four_git_runs(tree: Path, monkeypatch, count: int) -> None:
    """最後のラウンドの後に送った head は、共通の祖先・全差分・head の本文・ベースの本文を 1 回ずつ読む。"""
    base = _edited(tree, count)
    run = Run(git.Git(tree), base)
    run.last_target = _target(tree, base)
    changes.citations(run, run.last_target)
    for n in range(count):
        commit(tree, f"d{n}.md", "\n".join([*UNFENCED, WRONG, ""]), "囲みを消して足す")
    head = sh(tree, "rev-parse", "HEAD").strip()
    calls = count_git(monkeypatch)
    errors = changes.final_citations(run, head)
    assert errors is not None and len(errors) == 2 * count
    assert (len(calls), run.checked_head) == (4, head)


def test_added_lines_are_collected_once_per_file_however_many_citations(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """根拠の記述が何行あっても、足した行を求める差分の走査はファイルごとに 1 回で済む。"""
    base = _commit(tree)
    commit(tree, "many.md", "\n".join([*[WRONG] * 5, ""]), "足す")
    calls: list[str] = []
    read = DiffFile.added.fget
    assert read is not None
    monkeypatch.setattr(DiffFile, "added", property(lambda file: calls.append(file.path) or read(file)))
    assert len(_found(tree, base)) == 5
    assert calls == ["many.md"]


def test_issue_4090_3_another_mvv_is_checked_again(tree: Path) -> None:
    """同じ差分でも MVV が変われば確かめ直す。"""
    base = commit(tree, "note.txt", "x\n", "元")
    commit(tree, "a.md", f"{RIGHT}\n", "足す")
    run = Run(git.Git(tree), base)
    target = _target(tree, base)
    assert changes.citations(run, target) == []
    run.snapshot = _snapshot(version=12)
    errors = changes.citations(run, target)
    assert errors is not None and [e["kind"] for e in errors] == ["version"]
    assert len(run.citations) == 2


def test_files_without_citations_are_not_read(tree: Path, tmp_path: Path, monkeypatch) -> None:
    """根拠の記述にもコードブロックの囲みにも触れない変更では、差分のほかに本文を読まない。"""
    base = commit(tree, "lock.json", "{}\n", "元")
    commit(tree, "lock.json", '{"a": 1}\n', "変える")
    target = _full(_target(tree, base), tree, tmp_path)
    calls = count_git(monkeypatch)
    assert changes.citations(Run(git.Git(tree), base), target) == [] and calls == []


def test_issue_4090_3_failed_check_is_not_kept(tree: Path, monkeypatch) -> None:
    """確かめに失敗したら結果を残さず、確かめ終えた head も進めず、注意に載せる。次は読み直す。"""
    base = commit(tree, "note.txt", "x\n", "元")
    commit(tree, "a.md", f"{WRONG}\n", "足す")
    run = Run(git.Git(tree), base)
    target = _target(tree, base)
    with monkeypatch.context() as broken:
        _git_fails(broken, "review_diff")
        assert changes.citations(run, target) is None
    assert (run.citations, run.checked_head, len(run.warnings)) == ({}, None, 1)
    errors = changes.citations(run, target)
    assert errors is not None and len(errors) == 1


def test_unreadable_diff_file_is_a_failed_check(tree: Path, tmp_path: Path) -> None:
    """全差分のファイルを読めない・UTF-8 でないときも、例外を外へ出さない。"""
    base = _commit(tree)
    run = Run(git.Git(tree), base)
    target = _target(tree, base)._replace(full_diff=tmp_path / "missing.diff")
    assert changes.citations(run, target) is None
    (tmp_path / "missing.diff").write_bytes(b"\xff")
    assert changes.citations(run, target) is None


def test_end_without_a_round_is_checked_from_the_base(tree: Path) -> None:
    """1 つもラウンドを準備できていなくても、ベースとの共通の祖先から確かめる。"""
    base = commit(tree, "note.txt", "x\n", "元")
    commit(tree, "a.md", f"{WRONG}\n", "足す")
    errors = changes.final_citations(Run(git.Git(tree), base), sh(tree, "rev-parse", "HEAD").strip())
    assert errors is not None and [e["path"] for e in errors] == ["a.md"]
    missing = Run(git.Git(tree), base)
    assert changes.final_citations(missing, "0" * 40) is None and len(missing.warnings) == 1
