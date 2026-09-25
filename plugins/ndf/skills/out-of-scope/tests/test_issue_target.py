"""起票先の判断が 1 か所にあり、手順から引けることを固定する（#229）。

起票先を決める基準を持つのはこの Skill だけである。`retrospective` は参照だけを持つ。
判定の基準を持つ場所を 1 つにする考え方は `development-workflow` のモード判定と同じである。

読み取れないこと自体も失敗として扱う。表の書き方を変えるだけでこのチェックを無効にできる形に
しない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from issue_target_helpers import (
    REFERENCE,
    RESOLUTION_TABLE_HEADING,
    RUNTIME_LAYOUTS,
    SLUG,
    make_clone,
    read,
    resolution_snippet,
    run_resolution,
    runtime_layout,
)


@pytest.mark.parametrize("runtime", sorted(RUNTIME_LAYOUTS))
def test_the_second_stage_resolves_on_every_runtime(runtime: str, tmp_path: Path) -> None:
    """手順 2 は、手順書の表が挙げるどの位置でも配布元を決める（#306）。

    Kiro と agy は取得元を持たず、clone した作業ディレクトリがその位置になる。表だけを
    読むチェックでは、bash がその位置を見ていないことに気づけない。
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
    """取得元と現在地が違う配布元を指すときは、推測せず手順 3 へ倒す。"""
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
