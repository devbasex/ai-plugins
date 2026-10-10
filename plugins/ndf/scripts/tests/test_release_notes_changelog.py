"""配布の手順が plugin の README に触れず、更新情報を CHANGELOG.md の版の節だけへ書く（#1867）。

`release-steps.py changelog` と `notes` を、PR #1863（10.17.68 の配布で使ったスプリント PR）の実物の本文と、
子の箇条を持つ PR #1861 の本文の形で走らせる。偽の gh は `test_release_steps.fake_gh` を使う。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_release_steps import PY, SCRIPT, fake_gh, git  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import gh_sections  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_notes"
PR1863 = {int(k): v for k, v in json.loads((FIXTURES / "pr1863.json").read_text(encoding="utf-8")).items()}
BEFORE_10_17_68 = "89766449e"  # PR #1863 のマージ。10.17.68-dev.1 の配布の直前
CHANGES = "## 利用者向けの変化"

# PR #1861 の本文の「利用者向けの変化」（2 字下げの子の箇条 3 行）
PR1861_CHANGES = (
    "## 利用者向けの変化\n\n"
    "- ndf の PR 本文へ決定を写すとき、別ファイルに分けた決定も読み取られます。\n"
    "- ndf の design Skill では、決定を分けたファイルの形が次の 3 点に決まっています。\n"
    "  - ファイルの名前\n"
    "  - `## 決定の記録` の節\n"
    "  - `### 決定 N` の見出し\n"
)
PR1861_PARENT = "ndf の design Skill では、決定を分けたファイルの形が次の 3 点に決まっています。"
PR1861_ITEM = f"{PR1861_PARENT}（#1861）\n  - ファイルの名前\n  - `## 決定の記録` の節\n  - `### 決定 N` の見出し"
APPROVAL = (
    "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 版数 | 10.17.68 |\n"
    "| 配る中身 | （未記入） |\n| 検証への配布で確かめたこと | （未記入） |\n\n## 同意を求めること\n\n- [ ] x\n"
)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def release_repo(root: Path, readme: str, changelog: str, extra: dict[str, str] | None = None) -> Path:
    """ndf を配るリポジトリ（宣言・plugin.json・README・CHANGELOG.md）を作ってコミットする。"""
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    write(root, ".ndf/worktree.json", json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    write(root, ".ndf/supervise.json", json.dumps({"release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}}))
    write(root, "plugins/ndf/.claude-plugin/plugin.json", json.dumps({"name": "ndf", "version": "1.2.2"}))
    write(root, "plugins/ndf/README.md", readme)
    write(root, "CHANGELOG.md", changelog)
    for rel, text in (extra or {}).items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    return root / "plugins" / "ndf" / "README.md"


def step(root: Path, env: dict, sub: str, version: str, prs, *extra: str) -> dict:
    p = subprocess.run(
        [PY, str(SCRIPT), sub, "--root", str(root), "--version", version, "--prs", *map(str, prs), *extra],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def version_section(changelog: str, head: str) -> str:
    """CHANGELOG.md の `head`（日付を除いた見出し）で始まる版の節の中身。"""
    line = next(x for x in changelog.splitlines() if x.startswith(head + " "))
    return gh_sections.get_section(changelog, line) or ""


def show(rev: str, rel: str) -> str:
    p = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{rev}:{rel}"], capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip(f"{rev} を読めない（履歴の浅い clone）")
    return p.stdout


def test_pr1863_leaves_the_readme_and_keeps_it_under_the_line_limit(tmp_path):
    """受け入れ条件 1: 10.17.68 の配布の直前の README と CHANGELOG で changelog と notes を走らせても、README は変わらない。

    README の見出しは、前の bump が書き換えた後の形（v10.17.68-dev.1）にして、前の notes が書き込んだ状態を再現する。
    """
    readme = show(BEFORE_10_17_68, "plugins/ndf/README.md").replace("## v10.17.67 へ更新するとき", "## v10.17.68-dev.1 へ更新するとき")
    root = tmp_path / "repo"
    path = release_repo(
        root,
        readme,
        show(BEFORE_10_17_68, "CHANGELOG.md"),
        {"docs/glossary.md": show(BEFORE_10_17_68, "docs/glossary.md")},
    )
    before = path.read_bytes()
    env = fake_gh(tmp_path, PR1863)
    step(root, env, "changelog", "10.17.68-dev.1", [1863])
    res = step(root, env, "notes", "10.17.68-dev.1", [1863])
    assert path.read_bytes() == before
    assert [i["name"] for i in res["items"]] == ["CHANGELOG.md"]
    limit = subprocess.run(
        [PY, str(REPO_ROOT / "scripts" / "check-doc-line-limit.py"), "--root", str(root)], capture_output=True, text=True
    )
    assert limit.returncode == 0, limit.stdout + limit.stderr


def test_forty_lines_of_changes_all_go_to_the_version_section(tmp_path):
    """受け入れ条件 2・3: 40 行分の箇条と移行の手順は版の節へ全件並び、README は変わらない。"""
    lines = [f"- 変化 {i} の説明" for i in range(40)]
    body = f"{CHANGES}\n\n" + "\n".join(lines) + "\n\n## 移行の手順\n\n- `--old` を `--new` に置き換える\n- 導入し直す\n"
    root = tmp_path / "repo"
    path = release_repo(root, "# ndf\n\n## 使い方\n\nx\n", "# Changelog\n\n## [ndf 1.2.2] - 2025-12-01\n\n- 前の版\n")
    before = path.read_bytes()
    env = fake_gh(tmp_path, {7: {"title": "大きい変更", "body": body, "state": "MERGED"}})
    step(root, env, "changelog", "1.2.3-dev.1", [7])
    step(root, env, "notes", "1.2.3-dev.1", [7])
    assert path.read_bytes() == before
    section = version_section((root / "CHANGELOG.md").read_text(encoding="utf-8"), "## [ndf 1.2.3]")
    assert [line for line in section.splitlines() if line.startswith("- 変化 ")] == [f"{line}（#7）" for line in lines]
    assert gh_sections.get_section(section, "### 移行の手順") == "- `--old` を `--new` に置き換える（#7）\n- 導入し直す（#7）"


def test_child_items_stay_inside_their_parent():
    """受け入れ条件 9（I5）: 子の箇条は独立した項目にならず、（#番号）は親の 1 行目にだけ付く。"""
    items = gh_sections.section_items(PR1861_CHANGES, CHANGES, 1861)
    assert items == ["ndf の PR 本文へ決定を写すとき、別ファイルに分けた決定も読み取られます。（#1861）", PR1861_ITEM]
    # スプリント PR の本文（supervise_lib/pr.py が `- 項目` で並べる）を読み直しても同じ項目に戻る
    sprint = f"{CHANGES}\n\n" + "\n".join(f"- {i}" for i in items) + "\n"
    again = gh_sections.section_items(sprint, CHANGES, 1863)
    assert again[1] == PR1861_ITEM.replace("（#1861）", "（#1861）（#1863）", 1) and len(again) == 2


@pytest.mark.parametrize("indent", ["  ", " ", "    ", "\t"])
def test_child_items_are_one_level_deep_whatever_the_indent(indent):
    body = f"{CHANGES}\n\n- 親\n{indent}- 子\n{indent}* 子 2\n- 次\n"
    assert gh_sections.section_items(body, CHANGES, 5) == ["親（#5）\n  - 子\n  - 子 2", "次（#5）"]


def test_notes_twice_writes_the_same_changelog_with_nested_items(tmp_path):
    """受け入れ条件 8・9: 子の箇条は版の節に入れ子で並び、2 回目の notes は CHANGELOG.md を 1 バイトも変えない。"""
    root = tmp_path / "repo"
    release_repo(root, "# ndf\n", "# Changelog\n\n## [ndf 1.2.2] - 2025-12-01\n\n- 前の版\n")
    prs = {
        1861: {"title": "決定の形", "body": PR1861_CHANGES + "\n## 移行の手順\n\n- 無し\n", "state": "MERGED"},
        1862: {
            "title": "閉じる課題",
            "body": f"{CHANGES}\n\n- 閉じる課題が 0 件なら止まる\n\n## 移行の手順\n\n- `--issues` を足す\n",
            "state": "MERGED",
        },
    }
    env = fake_gh(tmp_path, prs)
    step(root, env, "changelog", "1.2.3-dev.1", [1861, 1862])
    step(root, env, "notes", "1.2.3-dev.1", [1861, 1862])
    first = (root / "CHANGELOG.md").read_bytes()
    step(root, env, "notes", "1.2.3-dev.1", [1861, 1862])
    assert (root / "CHANGELOG.md").read_bytes() == first
    section = version_section(first.decode("utf-8"), "## [ndf 1.2.3]")
    assert f"- {PR1861_ITEM}\n" in section
    assert "- ファイルの名前（#" not in section


def test_approval_from_pr1863_is_unchanged(tmp_path):
    """受け入れ条件 10（I6）: PR #1863 の本文で notes --approval が書く欄と節は、#1867 の前の出力と同じ。"""
    root = tmp_path / "repo"
    release_repo(root, "# ndf\n", "# Changelog\n\n## [ndf 10.17.68] - 2026-10-08\n\n- x\n", {"issues/approval.md": APPROVAL})
    env = fake_gh(tmp_path, PR1863)
    step(root, env, "notes", "10.17.68", [1863], "--approval", "issues/approval.md", "--verified", "claude,codex")
    want = (FIXTURES / "approval-1863.txt").read_text(encoding="utf-8")
    assert (root / "issues" / "approval.md").read_text(encoding="utf-8") == want
