"""起票先の上流リポジトリの解決が、どの配置でも同じ名前（か未決）を返すことを固定する（#229・#306・#851）。

解決は `issue-file.py resolve-target` が持つ。手順書の Markdown は読まず、部品を直接呼ぶ。
`gh` は `gh repo view` だけを見本の応答で答え、GitHub へは届かない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from issue_target_helpers import RUNTIME_LAYOUTS, SLUG, make_clone, resolved_upstream, run_resolution, runtime_layout


@pytest.mark.parametrize("runtime", sorted(RUNTIME_LAYOUTS))
def test_the_second_stage_resolves_on_every_runtime(runtime: str, tmp_path: Path) -> None:
    """手順 2 は、Claude Code・Codex の取得元と、Kiro / agy の現在地の clone のどれでも上流を決める。"""
    home, cwd = runtime_layout(tmp_path, runtime)

    assert resolved_upstream(home=home, cwd=cwd) == SLUG


def test_the_second_stage_sees_the_clone_from_a_directory_below_it(tmp_path: Path) -> None:
    """clone の下のディレクトリで実行しても、その clone が候補になる（現在地は clone の根とは限らない）。"""
    home = tmp_path / "home"
    home.mkdir()
    clone = make_clone(tmp_path / "clone")
    below = clone / "plugins" / "ndf" / "skills"
    below.mkdir(parents=True, exist_ok=True)

    assert resolved_upstream(home=home, cwd=below) == SLUG


def test_the_working_directory_is_dropped_when_it_does_not_carry_ndf(tmp_path: Path) -> None:
    """いま開いているリポジトリが上流とは限らない。無条件に採ると開発対象が上流として決まる。"""
    home = tmp_path / "home"
    home.mkdir()
    target = make_clone(tmp_path / "target", "https://github.com/example/app.git", carries_ndf=False)

    assert resolved_upstream(home=home, cwd=target) == ""


def test_github_sources_without_ndf_are_not_candidates(tmp_path: Path) -> None:
    """取得元の置き場所に並ぶ、`plugins/ndf/` を持たない GitHub の取得元は候補にしない。"""
    home, cwd = runtime_layout(tmp_path, "claude")
    marketplaces = home / ".claude/plugins/marketplaces"
    make_clone(marketplaces / "anthropic-agent-skills", "https://github.com/anthropics/skills.git", carries_ndf=False)
    make_clone(marketplaces / "a-first", "git@github.com:example/other.git", carries_ndf=False)

    assert resolved_upstream(home=home, cwd=cwd) == SLUG


def test_two_names_fall_through_to_the_third_stage(tmp_path: Path) -> None:
    """取得元と現在地が違う上流を指すときは、推測せず判断待ち（20）で候補を返す。"""
    home, _ = runtime_layout(tmp_path, "claude")
    fork = make_clone(tmp_path / "fork", "https://github.com/example/ai-plugins.git")

    code, obj = run_resolution(home=home, cwd=fork)

    assert code == 20 and obj["metrics"]["upstream"] is None
    assert sorted(i["repo"] for i in obj["items"]) == ["devbasex/ai-plugins", "example/ai-plugins"]


def test_a_fork_and_the_original_side_by_side_are_undecided(tmp_path: Path) -> None:
    """fork と本家の 2 つを取得元として登録した利用者では、どちらも `plugins/ndf/` を持つ。"""
    home, cwd = runtime_layout(tmp_path, "claude")
    make_clone(home / ".claude/plugins/marketplaces/ai-plugins-fork", "git@github.com:example/ai-plugins.git")

    assert resolved_upstream(home=home, cwd=cwd) == ""


def test_the_same_name_from_two_places_is_still_one_name(tmp_path: Path) -> None:
    """取得元の clone と現在地、Codex の取得元が同じ上流を指すときは 1 つにまとまる（SSH と HTTPS の差も含む）。"""
    home, _ = runtime_layout(tmp_path, "claude")
    make_clone(home / ".codex/.tmp/marketplaces/ai-plugins", "git@github.com:devbasex/ai-plugins.git")
    same = make_clone(tmp_path / "same", "https://github.com/devbasex/ai-plugins")

    assert resolved_upstream(home=home, cwd=same) == SLUG


def test_non_github_and_originless_clones_are_not_candidates(tmp_path: Path) -> None:
    home, cwd = runtime_layout(tmp_path, "claude")
    marketplaces = home / ".claude/plugins/marketplaces"
    make_clone(marketplaces / "gitlab", "https://gitlab.com/example/ai-plugins.git")
    make_clone(marketplaces / "no-origin", None)

    assert resolved_upstream(home=home, cwd=cwd) == SLUG


def test_the_environment_variable_wins_over_the_clones(tmp_path: Path) -> None:
    home, _ = runtime_layout(tmp_path, "claude")
    fork = make_clone(tmp_path / "fork", "https://github.com/example/ai-plugins.git")

    code, obj = run_resolution(home=home, cwd=fork, env_repo="example/ai-plugins", target="example/app")

    assert code == 0
    assert obj["metrics"] == {"upstream": "example/ai-plugins", "target": "example/app", "same": False, "source": "env"}


def test_the_development_repository_is_returned_beside_the_upstream(tmp_path: Path) -> None:
    home, cwd = runtime_layout(tmp_path, "claude")

    code, obj = run_resolution(home=home, cwd=cwd, target=SLUG)

    assert code == 0 and obj["metrics"]["same"] is True and obj["metrics"]["source"] == "clone"
