"""agy の初期一覧を予算の判定へ入れる（#215 の受け入れ条件 A10）。

配布先が 1 つ増えても、`manifests/agy-skills.txt` を読む経路と予算を突き合わせる経路が
無ければ、agy の一覧だけが判定されないまま増え続ける。**予算そのものは Claude Code から
借りた値である**（agy に規定が無いため。`scripts/check-skill-frontmatter.py` の注記）。
借りた事実は注記に残し、ここでは「借りた値で判定が働くこと」を固定する。

一時ディレクトリへ作った木に対して実行する。実物の Skill は読むだけで、書き換えない。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-frontmatter.py"

FAMILY = "ndf"


def write_skill(skills_dir: Path, name: str, description: str) -> None:
    (skills_dir / name).mkdir(parents=True, exist_ok=True)
    (skills_dir / name / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def build_tree(base: Path, count: int, description: str) -> Path:
    """agy の manifest を持つ木を作り、その `skills/` を返す。"""
    plugin = base / f"plugins/{FAMILY}"
    skills_dir = plugin / "skills"
    names = [f"probe-{index:03d}" for index in range(count)]
    for name in names:
        write_skill(skills_dir, name, description)
    (plugin / "manifests").mkdir(parents=True, exist_ok=True)
    for runtime in ("claude", "codex", "kiro", "agy"):
        (plugin / f"manifests/{runtime}-skills.txt").write_text(
            "".join(f"{name}\n" for name in names), encoding="utf-8"
        )
    return skills_dir


def run_checker(skills_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--skills-dir", str(skills_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def report_row(output: str, runtime: str) -> list[str]:
    for line in output.splitlines():
        columns = line.split()
        if columns and columns[0] == runtime:
            return columns
    raise AssertionError(f"{runtime} の行が出力に無い\n{output}")


def test_report_lists_agy_with_a_budget(tmp_path: Path) -> None:
    """報告に agy の行が出て、上限を持つ。"""
    skills_dir = build_tree(tmp_path, count=3, description="Probe skill for the listing budget.")
    result = run_checker(skills_dir, "--report")

    assert result.returncode == 0, result.stdout + result.stderr
    row = report_row(result.stdout, "agy")
    assert row[1].isdigit() and int(row[1]) > 0, row
    assert row[2].isdigit() and int(row[2]) > 0, row


def test_agy_borrows_the_claude_budget(tmp_path: Path) -> None:
    """上限は Claude Code と同じ値である（同じコンテキスト長の規定を借りている）。"""
    skills_dir = build_tree(tmp_path, count=3, description="Probe skill for the listing budget.")
    result = run_checker(skills_dir, "--report")

    assert report_row(result.stdout, "agy")[2] == report_row(result.stdout, "claude")[2]


def test_agy_listing_counts_the_path(tmp_path: Path) -> None:
    """agy の一覧はパスを含む側で見積もる。Claude Code の合計より大きくなる。

    一覧の構成に公式の記述が無いため、多い側で見積もる（Kiro CLI と同じ扱い）。
    """
    skills_dir = build_tree(tmp_path, count=3, description="Probe skill for the listing budget.")
    result = run_checker(skills_dir, "--report")

    agy = int(report_row(result.stdout, "agy")[1])
    claude = int(report_row(result.stdout, "claude")[1])
    assert agy > claude, result.stdout


def test_agy_budget_overflow_is_reported(tmp_path: Path) -> None:
    """予算を超えると agy の指摘が出る。読み取るだけで判定していない状態を弾く。"""
    skills_dir = build_tree(tmp_path, count=60, description="x" * 900)
    result = run_checker(skills_dir)

    output = result.stdout + result.stderr
    assert "ops/agy-listing" in output, output
    assert result.returncode != 0


def test_no_agy_manifest_means_no_agy_row(tmp_path: Path) -> None:
    """`agy-skills.txt` を持たない木では agy の行が出ない。

    manifest が唯一の配布の基準であることを、無い側でも確かめる。
    """
    skills_dir = build_tree(tmp_path, count=3, description="Probe skill for the listing budget.")
    (skills_dir.parent / "manifests/agy-skills.txt").unlink()
    result = run_checker(skills_dir, "--report")

    assert result.returncode == 0, result.stdout + result.stderr
    with_agy = [line for line in result.stdout.splitlines() if line.split()[:1] == ["agy"]]
    assert not with_agy, result.stdout
