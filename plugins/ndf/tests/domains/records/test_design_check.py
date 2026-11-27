"""設計の検査（`ndf records design check`。#2912 の受け入れ条件 4・決定 1）。

判定（`design_check_rules`）は文字列だけで、集め方（`design_check`）は Git のアダプターを差し替えた偽物で確かめる。git は起動しない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from ndf.adapters import git
from ndf.domains.records import cli, design_check
from ndf.domains.records import design_check_rules as dc

TABLE = "\n".join(
    [
        "## 人へ残す判断",
        "",
        "| 判断 | 当たるレッドライン | なぜ当たるか |",
        "| --- | --- | --- |",
        "| 認可の役割を足す | `C2` | 認可を変える |",
    ]
)
ROWS = dc.decision_rows(TABLE)


def _doc(*lines: str, section: str = "決定の記録") -> str:
    return "\n".join(["# 設計", "", f"## {section}", "", *lines, "", TABLE, ""])


# --- 委ねる語（設計書の「入出力のコントラクト」の行の例） ---

HITS = [
    "利用者が決めます", "利用者が確認します", "利用者に委ねます", "利用者の判断を待つ", "利用者の判断を待ちます",
    "承認ゲート 1 で利用者が選定する", "承認ゲートで人が決めてから進める", "承認ゲートで利用者が確かめてからマージする",
    "承認ゲートで人が選択しないか確かめる",
    "利用者が決める", "人が決める", "利用者が後で決める", "承認ゲートで人が選ぶ", "承認ゲートで利用者が選択する", "利用者が確認する",
    "利用者に委ねる", "利用者に委ねている", "利用者に委ねたままにする", "利用者が決めるまで進めない",
    "利用者が決めることはないか確かめる", "利用者に委ねるのではないか", "利用者に委ねるのではなく人が決める",
]  # fmt: skip
MISSES = [
    "利用者が決めた", "承認ゲートで利用者が選んだ", "承認ゲート 1 で利用者が選定した", "利用者に委ねた結果、A を採った",
    "利用者が決めました", "利用者の判断を待った", "利用者の判断を待つ必要はない",
    "利用者が決める必要はない", "利用者が決めるまでもない", "利用者が決めることはない", "利用者に委ねるのではなく AI が決める",
    "承認ゲートで人が決めずに進める", "承認ゲートで人が選びません", "承認ゲートで人が選択しない", "承認ゲートで人が選ばない",
    "承認ゲートで利用者が選定せずに進める",
    "本人が決める", "個人が決める", "`利用者が決める` という語",
]  # fmt: skip


@pytest.mark.parametrize("line", HITS)
def test_delegating_lines_are_found(line: str) -> None:
    assert dc.delegates(line)
    assert [f.text for f in dc.findings(_doc(line), "d.md")] == [line]


@pytest.mark.parametrize("line", MISSES)
def test_settled_negated_and_non_delegating_lines_pass(line: str) -> None:
    assert not dc.delegates(line)
    assert dc.findings(_doc(line), "d.md") == []


# --- 番号を名指しした行 ---


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("認可の役割を足すかは C2 に当たるため、利用者が決める", False),
        ("認可の役割を足すかは `C2` に当たるため、利用者が決める", False),
        ("一覧の表示順は C2 を参照し、利用者が決める", True),
        ("認可の役割を足すかは C2 には当たらないため、利用者が決める", True),
        ("認可の役割を足すかは `C2` に該当しないため、利用者が決める", True),
        ("認可の役割を足すかは C6 に当たるため、利用者が決める", True),
    ],
)
def test_named_lines_need_the_row_decision_and_its_number(line: str, found: bool) -> None:
    assert bool(dc.findings(_doc(line), "d.md")) is found


def test_an_empty_decision_row_is_not_a_partner_and_is_found_itself() -> None:
    text = TABLE + "\n| | C2 | 理由 |\n\n一覧の表示順は C2 に当たるため、利用者が決める\n"
    found = dc.findings(text, "d.md")
    assert [f.line for f in found] == [6, 8]


@pytest.mark.parametrize(
    "cells", ["| | C2 | 理由 |", "| 判断 | | 理由 |", "| 判断 | 要確認 | 理由 |", "| 判断 | C2 | — |", "| ** | C2 | 理由 |"]
)
def test_table_rows_missing_a_cell_are_found(cells: str) -> None:
    found = dc.findings(TABLE + "\n" + cells + "\n", "d.md")
    assert [(f.path, f.line, f.text) for f in found] == [("d.md", 6, cells)]


def test_a_table_without_the_column_headers_is_found_as_missing_cells() -> None:
    text = "## 人へ残す判断\n\n| a | b |\n| --- | --- |\n| 認可 | C2 |\n"
    assert [f.line for f in dc.findings(text, "d.md")] == [5]
    assert dc.decision_rows(text) == [dc.DecisionRow(5, "", (), "")]


def test_redline_rows_returns_only_rows_with_numbers() -> None:
    text = TABLE + "\n| 表示順 | | 理由 |\n| 削除 | C4・`P1` | 戻せない |\n"
    assert [(r.decision, r.numbers) for r in dc.redline_rows(text)] == [("認可の役割を足す", ("C2",)), ("削除", ("C4", "P1"))]
    assert dc.redline_rows("# 題\n\n| 判断 | 当たるレッドライン | なぜ当たるか |\n| - | - | - |\n| a | C2 | b |\n") == []


def test_lines_outside_the_sections_and_inside_code_are_not_found() -> None:
    text = "\n".join(
        ["# 設計", "", "## 処理の流れ", "", "利用者が決める", "", "## 未確認のまま残ること", "", "```", "利用者が決める", "```", ""]
    )
    assert dc.findings(text, "d.md") == []
    nested = "## 決定の記録\n\n### 決定 1\n\n利用者に委ねる\n\n## 用語\n\n利用者に委ねる\n"
    assert [f.line for f in dc.findings(nested, "d.md")] == [5]


def test_lines_limit_the_findings_to_the_changed_lines() -> None:
    """差分の外の欄を欠く表の行と番号の無い委ね方は、`lines` の中に正常な行しか無ければ指摘しない。"""
    text = "\n".join(
        [
            "## 人へ残す判断",  # 1
            "",  # 2
            "| 判断 | 当たるレッドライン | なぜ当たるか |",  # 3
            "| --- | --- | --- |",  # 4
            "| 認可の役割を足す | C2 | 認可を変える |",  # 5
            "| 表示順 | | |",  # 6
            "",  # 7
            "利用者が決める",  # 8
            "認可の役割を足すかは C2 に当たるため、利用者が決める",  # 9
        ]
    )
    assert [f.line for f in dc.findings(text, "d.md")] == [6, 8]
    assert dc.findings(text, "d.md", frozenset({5, 9})) == []
    assert dc.findings(text, "d.md", frozenset()) == []


# --- 差分の行と見出し ---


def test_added_lines_reads_hunks_including_omitted_counts() -> None:
    patch = "diff --git a/d.md b/d.md\n--- a/d.md\n+++ b/d.md\n@@ -3,0 +4,2 @@\n+a\n+b\n@@ -9 +11 @@\n-x\n+y\n@@ -20,2 +22,0 @@\n-p\n-q\n"
    assert dc.added_lines(patch) == frozenset({4, 5, 11})
    assert dc.added_lines("@@ -1 +1 @@ x\n-a\n+b\n") == frozenset({1})
    assert dc.added_lines("") == frozenset()
    assert dc.added_lines("@@ broken @@\n+a\n") is None


def test_heading_touched_sees_added_changed_and_removed_headings() -> None:
    before = "# 題\n\n段落\n\n## 決定の記録\n"
    after = "# 題\n\n## 未確認のまま残ること\n\n段落\n\n## 決定の記録\n"
    assert dc.heading_touched(before, after, "@@ -1,0 +3,2 @@\n+## 未確認のまま残ること\n+\n")
    assert dc.heading_touched(before, "# 題\n\n段落\n\n## 用語\n", "@@ -5 +5 @@\n-## 決定の記録\n+## 用語\n")
    assert dc.heading_touched(before, "# 題\n\n段落\n", "@@ -4,2 +3,0 @@\n-\n-## 決定の記録\n")
    assert not dc.heading_touched(before, "# 題\n\n文\n\n## 決定の記録\n", "@@ -3 +3 @@\n-段落\n+文\n")
    assert dc.heading_touched(before, before, "@@ x @@\n")


def test_anchors_lost_when_a_partner_row_disappears() -> None:
    row = "| 認可の役割を足す | `C2` | 認可を変える |"
    before = TABLE
    assert not dc.anchors_lost(before, before + "\n| 削除 | C4 | 戻せない |")
    assert not dc.anchors_lost(before, before.replace("認可を変える", "役割の付与を変える"))
    assert dc.anchors_lost(before, before.replace(row, ""))
    assert dc.anchors_lost(before, before.replace("認可の役割を足す", "役割を足す"))
    assert dc.anchors_lost(before, before.replace("`C2`", "C6"))
    assert dc.anchors_lost(before, before.replace("認可を変える", "—"))
    assert not dc.anchors_lost("# 題\n", "# 題\n")


def test_blank_and_numbers() -> None:
    assert dc.blank(" ** ") and dc.blank("—") and dc.blank("`-`") and not dc.blank("要確認")
    assert dc.numbers_in("C2 と P10、R3 と AC1 と C2", negatable=False) == ("C2", "P10", "R3")
    assert dc.numbers_in("C2 に当たらない・C4 に当たる", negatable=True) == ("C4",)


# --- 集め方（from_base・from_files・check と CLI） ---


FORK = "f" * 40
START = "s" * 40
D1, D2, D3 = "issues/issue-1-design.md", "issues/issue-2-design.md", "issues/issue-3-design-detail.md"
CLEAN = "## 決定の記録\n\nA を採る\n"


class FakeGit:
    """Git のアダプターの偽物。3（name-status と未追跡）と 4（-U0）と 5（blobs）の出力を文字列で返し、起動を記録する。"""

    def __init__(
        self,
        name_status: str = "",
        untracked: str = "",
        patch: str = "",
        before: Optional[dict[str, str]] = None,
        refs: Optional[dict[str, str]] = None,
        fail: Optional[str] = None,
    ) -> None:
        self.name_status, self.untracked, self.patch = name_status, untracked, patch
        self.before = before or {}
        self.refs = refs if refs is not None else {"refs/remotes/origin/main": START}
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    def commit_or_none(self, ref: str) -> Optional[str]:
        return self.refs.get(ref)

    def merge_base(self, a: str, b: str) -> str:
        self.calls.append(("merge-base", a, b))
        if self.fail == "merge-base":
            raise git.GitError("fatal: no merge base")
        return FORK

    def run(self, *args: str) -> str:
        self.calls.append(args)
        if args == ("remote",):
            return "origin\n"
        if args[0] == "ls-files":
            return self.untracked
        if "--name-status" in args:
            return self.name_status
        if "-U0" in args:
            return self.patch
        raise AssertionError(args)

    def blobs(self, *names: str) -> list[Optional[bytes]]:
        self.calls.append(("blobs", *names))
        return [self.before[n.split(":", 1)[1]].encode() if n.split(":", 1)[1] in self.before else None for n in names]


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _base(tmp_path: Path, fake: FakeGit) -> dict[str, Optional[frozenset[int]]]:
    return {t.path: t.lines for t in design_check.from_base(tmp_path, "main", "origin", git_of=lambda _: fake)}  # type: ignore[arg-type,return-value]


def _hunk(path: str, header: str, *body: str, old: Optional[str] = None) -> str:
    lines = [f"diff --git a/{old or path} b/{path}"]
    if old is not None:
        lines += ["similarity index 90%", f"rename from {old}", f"rename to {path}"]
    lines += [f"--- a/{old or path}", f"+++ b/{path}", header, *body]
    return "\n".join(lines) + "\n"


def test_from_base_collects_added_modified_renamed_and_untracked_but_not_deleted(tmp_path: Path) -> None:
    after1 = CLEAN + "利用者が決める\n"
    _tree(tmp_path, {D1: after1, D2: CLEAN, "issues/issue-9-design.md": CLEAN, "docs/a.md": "利用者が決める\n", D3: CLEAN})
    fake = FakeGit(
        name_status=f"M\0{D1}\0A\0{D2}\0D\0issues/issue-5-design.md\0M\0docs/a.md\0R095\0issues/issue-7-design.md\0{D3}\0",
        untracked="issues/issue-9-design.md\0",
        patch=_hunk(D1, "@@ -3,0 +4 @@", "+利用者が決める") + _hunk(D3, "@@ -3 +3 @@", "-A", "+A を採る", old="issues/issue-7-design.md"),
        before={D1: CLEAN, "issues/issue-7-design.md": "## 決定の記録\n\nA\n"},
    )
    assert _base(tmp_path, fake) == {D1: frozenset({4}), D2: None, D3: frozenset({3}), "issues/issue-9-design.md": None}
    diff = [c for c in fake.calls if "-U0" in c]
    assert len(diff) == 1 and diff[0][-3:] == (D1, "issues/issue-7-design.md", D3)
    assert all(a in diff[0] for a in ("--find-renames", "--no-ext-diff", "--no-textconv", "--no-color", "--inter-hunk-context=0"))
    assert ("-c", "core.quotePath=false") == diff[0][:2]
    assert ("blobs", f"{FORK}:{D1}", f"{FORK}:issues/issue-7-design.md") in fake.calls
    assert ("merge-base", START, "HEAD") in fake.calls
    found = design_check.check(design_check.from_base(tmp_path, "main", "origin", git_of=lambda _: fake))  # type: ignore[arg-type,return-value]
    assert [i["name"] for i in found["items"]] == [f"{D1}:4"]


def test_from_base_with_no_targets_is_ok(tmp_path: Path) -> None:
    fake = FakeGit(name_status="M\0docs/a.md\0")
    assert _base(tmp_path, fake) == {}
    assert not any("-U0" in c or c[0] == "blobs" for c in fake.calls)
    assert design_check.check([])["status"] == "ok"


@pytest.mark.parametrize(
    ("before", "after", "patch"),
    [
        # 表の行を消した
        (
            TABLE + "\n\n認可の役割を足すかは C2 に当たるため、利用者が決める\n",
            "## 人へ残す判断\n\n認可の役割を足すかは C2 に当たるため、利用者が決める\n",
            "@@ -3,3 +2,0 @@\n-a\n-b\n-c\n",
        ),
        # 見出しを足した
        (
            "# 題\n\n利用者が決める\n",
            "# 題\n\n## 未確認のまま残ること\n\n利用者が決める\n",
            "@@ -2,0 +3,2 @@\n+## 未確認のまま残ること\n+\n",
        ),
        # 読めない hunk の見出し
        (CLEAN, CLEAN + "利用者が決める\n", "@@ broken @@\n+利用者が決める\n"),
    ],
    ids=["row-removed", "heading-added", "broken-hunk"],
)
def test_from_base_reads_every_line_when_partners_or_sections_change(tmp_path: Path, before: str, after: str, patch: str) -> None:
    _tree(tmp_path, {D1: after})
    fake = FakeGit(name_status=f"M\0{D1}\0", patch=_hunk(D1, *patch.splitlines()), before={D1: before})
    assert _base(tmp_path, fake) == {D1: None}
    assert design_check.check(design_check.from_base(tmp_path, "main", "origin", git_of=lambda _: fake))["status"] == "stopped"  # type: ignore[arg-type,return-value]


def test_from_base_sees_only_added_rows_when_a_row_is_added(tmp_path: Path) -> None:
    after = TABLE + "\n| 削除 | C4 | 戻せない |\n"
    _tree(tmp_path, {D1: after})
    fake = FakeGit(name_status=f"M\0{D1}\0", patch=_hunk(D1, "@@ -5,0 +6 @@", "+| 削除 | C4 | 戻せない |"), before={D1: TABLE + "\n"})
    assert _base(tmp_path, fake) == {D1: frozenset({6})}


def test_pure_rename_sees_no_line(tmp_path: Path) -> None:
    _tree(tmp_path, {D1: CLEAN + "利用者が決める\n"})
    patch = f"diff --git a/{D2} b/{D1}\nsimilarity index 100%\nrename from {D2}\nrename to {D1}\n"
    fake = FakeGit(name_status=f"R100\0{D2}\0{D1}\0", patch=patch, before={D2: CLEAN + "利用者が決める\n"})
    assert _base(tmp_path, fake) == {D1: frozenset()}


def test_paths_with_spaces_and_quotes_are_bound_to_their_targets(tmp_path: Path) -> None:
    spaced, tabbed = "issues/issue-1-design a b.md", "issues/issue-2-design\tx.md"
    _tree(tmp_path, {spaced: CLEAN + "x\n", tabbed: CLEAN + "y\n"})
    patch = (
        f"diff --git a/{spaced} b/{spaced}\n--- a/{spaced}\t\n+++ b/{spaced}\t\n@@ -3,0 +4 @@\n+x\n"
        'diff --git "a/issues/issue-2-design\\tx.md" "b/issues/issue-2-design\\tx.md"\n'
        '--- "a/issues/issue-2-design\\tx.md"\n+++ "b/issues/issue-2-design\\tx.md"\n@@ -3,0 +4 @@\n+y\n'
    )
    fake = FakeGit(name_status=f"M\0{spaced}\0M\0{tabbed}\0", patch=patch, before={spaced: CLEAN, tabbed: CLEAN})
    assert _base(tmp_path, fake) == {spaced: frozenset({4}), tabbed: frozenset({4})}
    renamed = f"diff --git a/{D2} b/{spaced}\nrename from {D2}\nrename to {spaced}\n"
    assert design_check.hunk_parts(renamed)[spaced].old == D2


def test_unbound_and_mismatched_targets_read_every_line(tmp_path: Path) -> None:
    _tree(tmp_path, {D1: CLEAN, D2: CLEAN})
    # D1 は 4 の部分に結び付かない（モードだけの変更）。D2 は 3 では変更なのに 4 では改名
    patch = f"diff --git a/{D1} b/{D1}\nold mode 100644\nnew mode 100755\n" + _hunk(D2, "@@ -3 +3 @@", "-a", "+b", old=D3)
    fake = FakeGit(name_status=f"M\0{D1}\0M\0{D2}\0", patch=patch, before={D1: CLEAN, D2: CLEAN})
    assert _base(tmp_path, fake) == {D1: None, D2: None}


@pytest.mark.parametrize(
    ("fake", "word"),
    [
        (FakeGit(refs={}), "どちらも読めない"),
        (FakeGit(fail="merge-base"), "no merge base"),
    ],
    ids=["no-start", "no-fork"],
)
def test_from_base_failures_are_preconditions(tmp_path: Path, fake: FakeGit, word: str) -> None:
    with pytest.raises(design_check.DesignCheckError) as caught:
        _base(tmp_path, fake)
    assert word in str(caught.value)


def test_from_base_uses_the_local_branch_when_the_tracking_branch_is_missing(tmp_path: Path) -> None:
    fake = FakeGit(refs={"refs/heads/main": START})
    assert _base(tmp_path, fake) == {}


def test_from_files_reads_every_line_of_any_name(tmp_path: Path) -> None:
    _tree(tmp_path, {"docs/run.md": "## 決定の記録\n\n利用者が決める\n", "e.md": ""})
    targets = design_check.from_files(tmp_path, ["docs/run.md", "e.md"])
    assert [(t.path, t.lines) for t in targets] == [("docs/run.md", None), ("e.md", None)]
    found = design_check.check(targets)
    assert found["cause"]["kind"] == "check_failed"
    assert found["items"] == [{"name": "docs/run.md:3", "rule": "unnamed_redline", "text": "利用者が決める"}]
    with pytest.raises(design_check.DesignCheckError):
        design_check.from_files(tmp_path, ["gone.md"])


def _cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(cli, "_root", lambda args: tmp_path)
    return cli.main(["design", "check", *argv])


def test_cli_writes_the_same_result_and_rejects_bad_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tree(tmp_path, {"d.md": "## 決定の記録\n\n利用者が決める\n", "ok.md": CLEAN})
    out = tmp_path / "state/check.json"
    code = _cli(tmp_path, monkeypatch, "--result", str(out), "d.md")
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and json.loads(out.read_text(encoding="utf-8")) == printed
    assert _cli(tmp_path, monkeypatch, "ok.md") == 0
    assert _cli(tmp_path, monkeypatch) == 2
    assert _cli(tmp_path, monkeypatch, "--base", "main", "ok.md") == 2
    assert _cli(tmp_path, monkeypatch, "gone.md") == 3


def test_cli_result_replaces_only_a_previous_result_and_keeps_it_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--result` は前の検査結果だけを置き換え、0600 で書く。検査した文書・ほかのファイル・リンクは書かずに invalid（2）。"""
    _tree(tmp_path, {"d.md": "## 決定の記録\n\n利用者が決める\n", "note.txt": "残す\n"})
    out = tmp_path / "state/check.json"
    assert _cli(tmp_path, monkeypatch, "--result", str(out), "d.md") == 1
    assert _cli(tmp_path, monkeypatch, "--result", str(out), "d.md") == 1  # 前の結果は置き換える
    assert out.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in out.parent.iterdir()] == ["check.json"]  # 一時ファイルを残さない
    capsys.readouterr()
    assert _cli(tmp_path, monkeypatch, "--result", str(tmp_path / "d.md"), "d.md") == 2
    assert _cli(tmp_path, monkeypatch, "--result", str(tmp_path / "note.txt"), "d.md") == 2
    link = tmp_path / "state/link.json"
    link.symlink_to(tmp_path / "elsewhere.json")  # リンク先が無いリンク
    assert _cli(tmp_path, monkeypatch, "--result", str(link), "d.md") == 2
    assert (tmp_path / "d.md").read_text(encoding="utf-8").startswith("## 決定の記録")
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "残す\n"
    assert not (tmp_path / "elsewhere.json").exists()
