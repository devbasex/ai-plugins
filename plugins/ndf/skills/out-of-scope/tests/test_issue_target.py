"""起票先の判断が 1 か所にあり、手順から引けることを固定する（#229）。

起票先を決める基準を持つのはこの Skill だけである。`retrospective` は参照だけを持つ。
判定の基準を持つ場所を 1 つにする考え方は `development-workflow` のモード判定と同じである。

読み取れないこと自体も失敗として扱う。表の書き方を変えるだけでこの検査を無効にできる形に
しない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from issue_target_helpers import (
    CROSS_REPOSITORY_HEADING,
    DECISION_TABLE_HEADING,
    REFERENCE,
    RESOLUTION_TABLE_HEADING,
    RUNTIME_LAYOUTS,
    SKILL,
    SLUG,
    gh_issue_commands,
    headings,
    make_clone,
    ordered_steps,
    read,
    resolution_snippet,
    run_resolution,
    runtime_layout,
    section,
    table,
)

# 手順の見出しの並び。**起票先を決める段は 3 択の直後に来る。** 起票先が要るのは
# 「起票する」を選んだときだけで、重複の確認もその起票先に対して行う。
EXPECTED_STEPS = [
    "1. 範囲かを照合する",
    "2. 3 択で決める",
    "3. 起票先を決める",
    "4. 重複を確かめる",
    "5. 起票する",
    "6. 由来を残す",
]


def test_the_decision_table_lists_three_kinds_and_their_target() -> None:
    """判断表は 3 つの性質と、それぞれの起票先を持つ。"""
    header, rows = table(read(REFERENCE), DECISION_TABLE_HEADING)
    assert header[1] == "起票先", f"2 列目が起票先ではない: {header}"
    assert len(rows) == 3, f"判断表の行数が 3 ではない: {rows}"
    assert all(row[0] for row in rows), f"性質が空の行がある: {rows}"
    assert all("リポジトリ" in row[1] for row in rows), f"起票先がリポジトリを指していない: {rows}"


def test_the_resolution_table_has_three_stages() -> None:
    """起票先のリポジトリの解決は 3 段で、段 3 は止まる側に倒す。"""
    _, rows = table(read(REFERENCE), RESOLUTION_TABLE_HEADING)
    assert [row[0] for row in rows] == ["1", "2", "3"], f"段の並びが違う: {rows}"
    assert "NDF_SKILL_REPO" in rows[0][1], f"段 1 が環境変数を見ていない: {rows[0]}"
    assert "remote.origin.url" in rows[1][1], f"段 2 が取得元の clone を見ていない: {rows[1]}"


def test_the_second_stage_only_looks_at_clones_that_carry_ndf() -> None:
    """段 2 は、NDF の実体を持つ clone だけを候補にする。

    取得元の位置には登録したすべての clone が並ぶ。GitHub の取得元であることだけを条件に
    すると、Slack など配布元ではないリポジトリが候補に入る。
    """
    _, rows = table(read(REFERENCE), RESOLUTION_TABLE_HEADING)
    assert "plugins/ndf" in rows[1][1], f"段 2 が配布元へ絞っていない: {rows[1]}"
    assert "plugins/ndf" in resolution_snippet(read(REFERENCE)), "解決が配布元へ絞っていない"


def test_the_second_stage_does_not_adopt_inside_the_loop() -> None:
    """候補の採用を、走査の途中で行わない。

    ループの中で決めると、先頭から見て最初に条件へ合った取得元が採られる。並びは名前順で
    あって、配布元が先に来る保証は無い。複数残るときは段 3 へ倒す。
    """
    code = resolution_snippet(read(REFERENCE))
    assert "for clone" in code and "done" in code, f"取得元の走査が読み取れない: {code}"
    loop = code[code.index("for clone") : code.index("done")]
    assert "SKILL_REPO=" not in loop, f"走査の途中で採用している: {loop}"


@pytest.mark.parametrize("runtime", sorted(RUNTIME_LAYOUTS))
def test_the_second_stage_resolves_on_every_runtime(runtime: str, tmp_path: Path) -> None:
    """段 2 は、手順書の表が挙げるどの位置でも配布元を決める（#306）。

    Kiro と agy は取得元を持たず、clone した作業ディレクトリがその位置になる。表だけを
    読む検査では、bash がその位置を見ていないことに気づけない。
    """
    home, cwd = runtime_layout(tmp_path, runtime)

    assert run_resolution(read(REFERENCE), home=home, cwd=cwd) == SLUG


def test_the_second_stage_sees_the_clone_from_a_directory_below_it(tmp_path: Path) -> None:
    """clone の下のディレクトリで実行しても、その clone が候補になる。

    取得元を持たないランタイムでは現在地が起点になる。**現在地は clone の根とは限らない。**
    """
    home = tmp_path / "home"
    home.mkdir()
    clone = make_clone(tmp_path / "clone")
    below = clone / "plugins" / "ndf" / "skills"
    below.mkdir(parents=True, exist_ok=True)

    assert run_resolution(read(REFERENCE), home=home, cwd=below) == SLUG


def test_the_working_directory_is_dropped_when_it_does_not_carry_ndf(tmp_path: Path) -> None:
    """いま開いているリポジトリが配布元とは限らない。

    現在地を無条件に採ると、開発対象のリポジトリが配布元として決まる。
    """
    home = tmp_path / "home"
    home.mkdir()
    target = make_clone(tmp_path / "target", "https://github.com/example/app.git", carries_ndf=False)

    assert run_resolution(read(REFERENCE), home=home, cwd=target) == ""


def test_two_names_fall_through_to_the_third_stage(tmp_path: Path) -> None:
    """取得元と現在地が違う配布元を指すときは、推測せず段 3 へ倒す。"""
    home, _ = runtime_layout(tmp_path, "claude")
    fork = make_clone(tmp_path / "fork", "https://github.com/example/ai-plugins.git")

    assert run_resolution(read(REFERENCE), home=home, cwd=fork) == ""


def test_the_same_name_from_two_places_is_still_one_name(tmp_path: Path) -> None:
    """取得元の clone と現在地が同じ配布元を指すときは、1 つにまとまる。"""
    home, _ = runtime_layout(tmp_path, "claude")
    same = make_clone(tmp_path / "same")

    assert run_resolution(read(REFERENCE), home=home, cwd=same) == SLUG


def test_an_unreadable_resolution_snippet_fails() -> None:
    """解決の囲みを読み取れないことは、素通りではなく失敗になる。"""
    with pytest.raises(AssertionError):
        resolution_snippet(f"{RESOLUTION_TABLE_HEADING}\n\n本文だけの節\n")


def test_the_reference_covers_the_duplicate_check_and_the_creation() -> None:
    """重複の確認と起票と、既存の issue への追記が呼び出しとして載っている。"""
    commands = gh_issue_commands(read(REFERENCE))
    for command in ("gh issue list", "gh issue create", "gh issue comment"):
        assert any(line.startswith(command) for line in commands), f"呼び出しが無い: {command}"


@pytest.mark.parametrize("path", [REFERENCE, SKILL], ids=["reference", "skill"])
def test_every_gh_issue_call_passes_the_target(path: Path) -> None:
    """`gh issue` の呼び出しは、どのファイルのどの下位コマンドでも起票先を受け取る。

    `--repo` を省くと、いま作業しているリポジトリへ向かう。片方のファイルだけを見ると、
    もう片方から `--repo` が抜けたことに気づけない。
    """
    commands = gh_issue_commands(read(path))
    assert commands, f"`gh issue` の呼び出しを読み取れない: {path}"
    missing = [line for line in commands if "--repo" not in line]
    assert not missing, f"起票先が渡されていない: {missing}"


def test_the_steps_keep_their_order() -> None:
    """起票先を決める段が 3 択の直後にある。"""
    assert headings(read(SKILL), "### ") == EXPECTED_STEPS


def test_the_consent_shows_the_target() -> None:
    """起票の前に取る同意の提示に、起票先が含まれる。"""
    lines = [line for line in read(SKILL).splitlines() if "同意を取る" in line]
    assert lines, "同意を取る提示が見つからない"
    assert all("起票先" in line for line in lines), f"提示に起票先が無い: {lines}"


def test_the_skill_points_at_the_reference() -> None:
    """手順が判断表の置き場所を指している。"""
    assert "references/issue-target.md" in read(SKILL)


@pytest.mark.parametrize("heading", [DECISION_TABLE_HEADING, RESOLUTION_TABLE_HEADING])
def test_an_unreadable_table_fails(heading: str) -> None:
    """表を読み取れないことは、素通りではなく失敗になる。"""
    with pytest.raises(AssertionError):
        table("見出しの無い本文", heading)


def test_the_cross_repository_steps_start_from_the_skill_repository() -> None:
    """両方にまたがる課題は、配布元を先に起票する。

    番号が先に決まれば、開発対象の側の本文へその番号を書ける。逆順にすると、どちらの
    本文にも相手の番号が無い状態が一度できる。
    """
    steps = ordered_steps(read(REFERENCE), CROSS_REPOSITORY_HEADING)
    assert len(steps) == 3, f"手順の数が 3 ではない: {steps}"
    assert "配布元" in steps[0] and "起票" in steps[0], f"最初が配布元への起票ではない: {steps[0]}"
    assert "開発対象" in steps[1], f"2 番目が開発対象への起票ではない: {steps[1]}"


def test_the_cross_repository_steps_link_both_numbers() -> None:
    """双方の番号が互いから辿れる。開発対象の本文に配布元の番号、配布元にコメントで開発対象の番号。"""
    steps = ordered_steps(read(REFERENCE), CROSS_REPOSITORY_HEADING)
    assert "本文" in steps[1] and "配布元の番号" in steps[1], f"本文へ配布元の番号を書いていない: {steps[1]}"
    assert "配布元" in steps[2], f"3 番目が配布元への追記ではない: {steps[2]}"
    assert "開発対象の番号" in steps[2] and "コメント" in steps[2], f"開発対象の番号を残していない: {steps[2]}"


def test_the_cross_repository_comment_is_executable() -> None:
    """配布元へ番号を戻す追記が、起票先を受け取る呼び出しとして載っている。"""
    body = "\n".join(section(read(REFERENCE), CROSS_REPOSITORY_HEADING))
    commands = gh_issue_commands(body)
    found = [line for line in commands if line.startswith("gh issue comment")]
    assert found, f"追記の呼び出しが無い: {commands}"
    assert all("--repo" in line for line in found), f"起票先が渡されていない: {found}"


def test_the_skill_points_at_the_cross_repository_section() -> None:
    """手順が、両方にまたがる場合の節を名前で指している。"""
    assert CROSS_REPOSITORY_HEADING.lstrip("# ") in read(SKILL)


@pytest.mark.parametrize("heading", [CROSS_REPOSITORY_HEADING])
def test_an_unreadable_section_fails(heading: str) -> None:
    """節を読み取れないことは、素通りではなく失敗になる。"""
    with pytest.raises(AssertionError):
        ordered_steps("見出しの無い本文", heading)
