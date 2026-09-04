"""公開する Skill の本文に自リポジトリ前提が混入していないかの検査を確かめる。

記号（T1〜T6）は `issues/parallel-batch-07/06-issue-292.md` の「テスト設計」に対応する。

検査そのものは `scripts/check-skill-repo-assumptions.py` にある。実物の Skill は
書き換えず、一時ディレクトリへ最小の木を作ってそこを検査させる（T6 だけが実物を読む）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-repo-assumptions.py"

# 実物と重ならない Skill 名にしておく。テストが実物の一覧へ依存していないことを、
# 名前そのもので示すためである。
LISTED_SKILL = "alpha"
UNLISTED_SKILL = "bravo"

CLEAN_BODY = """# 見出し

対象リポジトリの検証手段を探して決める。コマンドを推測して組み立てない。
"""

ASSUMING_BODY = """# 見出し

修正で push した場合は `claude plugin validate` を通すこと。
"""


def build_tree(tmp_path: Path, *, listed: str, unlisted: str) -> Path:
    """`manifests/` と `skills/` を持つ最小の木を作り、`skills/` を返す。"""
    family = tmp_path / "plugins" / "fixture"
    manifests = family / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "claude-skills.txt").write_text(f"{LISTED_SKILL}\n", encoding="utf-8")

    skills = family / "skills"
    for name, body in ((LISTED_SKILL, listed), (UNLISTED_SKILL, unlisted)):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(body, encoding="utf-8")

    # `tests/` の下は走査の対象外である。対象外であることを、ヒット語を置いて示す。
    (skills / LISTED_SKILL / "tests").mkdir()
    (skills / LISTED_SKILL / "tests" / "fixture.md").write_text(ASSUMING_BODY, encoding="utf-8")
    return skills


def run_check(skills_dir: Path, exclusions: dict[str, str] | None = None,
              tmp_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """検査を起動する。`exclusions` を省くと、検査が持つ既定の宣言を使う。"""
    argv = [sys.executable, str(CHECKER), "--skills-dir", str(skills_dir)]
    if exclusions is not None:
        assert tmp_path is not None, "除外を差し替えるときは書き出す先が要る"
        path = tmp_path / "exclusions.json"
        path.write_text(json.dumps(exclusions, ensure_ascii=False), encoding="utf-8")
        argv += ["--exclusions", str(path)]
    return subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


# --- T1: 検知する ---


def test_detects_assumption_in_listed_skill(tmp_path: Path) -> None:
    """公開する Skill の本文にヒット語があれば終了コード 1 で終わる。"""
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {}, tmp_path)
    assert result.returncode == 1, output_of(result)
    out = output_of(result)
    assert f"{LISTED_SKILL}/SKILL.md" in out
    assert "claude plugin " in out
    assert ":3:" in out or ":3 " in out, f"行番号が出ていない: {out}"


def test_clean_tree_passes(tmp_path: Path) -> None:
    """ヒット語が無ければ終了コード 0 で終わる。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {}, tmp_path)
    assert result.returncode == 0, output_of(result)


# --- T2: 除外が効く ---


def test_excluded_file_passes(tmp_path: Path) -> None:
    """同じ本文でも、除外に載せたファイルなら終了コード 0 で終わる。"""
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {f"{LISTED_SKILL}/SKILL.md": "この文書の主題は NDF 自身である"}, tmp_path)
    assert result.returncode == 0, output_of(result)


# --- T3: 除外の理由が要る ---


@pytest.mark.parametrize("reason", ["", "   "])
def test_exclusion_without_reason_fails_the_check(tmp_path: Path, reason: str) -> None:
    """理由の無い除外は、ヒットの有無に関わらず検査自体を失敗させる。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {f"{LISTED_SKILL}/SKILL.md": reason}, tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "理由" in output_of(result)


# --- T4: 除外の陳腐化を検知する ---


def test_exclusion_pointing_at_missing_file_fails_the_check(tmp_path: Path) -> None:
    """実在しないファイルを除外に載せると、検査自体が失敗する。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {"charlie/SKILL.md": "消えた文書"}, tmp_path)
    assert result.returncode == 2, output_of(result)
    out = output_of(result)
    assert "charlie/SKILL.md" in out


def test_exclusion_outside_scan_scope_fails_the_check(tmp_path: Path) -> None:
    """実在しても走査の範囲外なら、除外として成立しないので失敗させる。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {f"{UNLISTED_SKILL}/SKILL.md": "配らない Skill"}, tmp_path)
    assert result.returncode == 2, output_of(result)
    assert f"{UNLISTED_SKILL}/SKILL.md" in output_of(result)


# --- T5: 走査の範囲 ---


def test_unlisted_skill_is_not_scanned(tmp_path: Path) -> None:
    """manifest に載らない Skill の本文は走査しない。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=ASSUMING_BODY)
    result = run_check(skills, {}, tmp_path)
    assert result.returncode == 0, output_of(result)


def test_tests_directory_is_not_scanned(tmp_path: Path) -> None:
    """`tests/` の下は走査しない（`build_tree` がヒット語を置いている）。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, {}, tmp_path)
    assert result.returncode == 0, output_of(result)
    assert "tests/fixture.md" not in output_of(result)


# --- T6: 実リポジトリで通る ---


def test_real_repository_passes() -> None:
    """実物の Skill でも通る。検査を入れた時点で落ちる状態を作らない。"""
    result = run_check(REPO_ROOT / "plugins/ndf/skills")
    assert result.returncode == 0, output_of(result)


def test_default_scan_covers_the_real_repository() -> None:
    """`--skills-dir` を省いても、実物の Skill を走査して通る。"""
    result = subprocess.run([sys.executable, str(CHECKER)],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, output_of(result)


def test_report_shows_scan_size() -> None:
    """`--report` が走査した本数とヒット数を出す（受け入れ条件 A9 の根拠）。"""
    result = subprocess.run([sys.executable, str(CHECKER), "--report"],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, output_of(result)
    out = output_of(result)
    assert "33" in out, f"公開する Skill の数が出ていない: {out}"
    assert "89" in out, f"走査した本数が出ていない: {out}"
