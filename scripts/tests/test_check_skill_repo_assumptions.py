"""公開する Skill とスクリプトに自リポジトリ前提が混入していないかのチェックを確かめる。

記号（T1〜T6）は `issues/parallel-batch-07/06-issue-292.md` の「テスト設計」に対応する。

チェックそのものは `scripts/check-skill-repo-assumptions.py` にある。実物の Skill は
書き換えず、一時ディレクトリへ最小の木を作ってそこをチェックさせる（T6 だけが実物を読む）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-repo-assumptions.py"
sys.path.insert(0, str(REPO_ROOT / "scripts/lib"))
from allowlist import allow_file_name  # noqa: E402

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


def allow_row(path: str, name: str, *, count: int = 1, kind: str = "not-a-violation", reason: str = "違反でない", issue=None) -> dict:
    """例外リストの 1 項目。`issue` を渡すと `pending` の形にする。"""
    row = {"path": path, "name": name, "kind": kind, "reason": reason, "count": count}
    if issue is not None:
        row |= {"kind": "pending", "issue": issue}
    return row


def write_allow(tmp_path: Path, rows: list[dict]) -> Path:
    """例外リストのディレクトリを作る。項目の形が違ってもそのまま書く（形の検査を確かめるため）。"""
    allow = tmp_path / "allow"
    allow.mkdir(exist_ok=True)
    for row in rows:
        (allow / allow_file_name(row)).write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return allow


def run_check(skills_dir: Path | None, allow: list[dict] | None = None, tmp_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """チェックを起動する。`allow` を省くと、チェックが持つ既定の例外リストを使う。

    `skills_dir` を None にすると `--skills-dir` を渡さず、family をすべて見る走査になる。
    """
    argv = [sys.executable, str(CHECKER)]
    if skills_dir is not None:
        argv += ["--skills-dir", str(skills_dir)]
    if allow is not None:
        assert tmp_path is not None, "例外リストを差し替えるときは書き出す先が要る"
        argv += ["--allow", str(write_allow(tmp_path, allow))]
    return subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)


def key_for(skills_dir: Path, rel: str) -> str:
    """例外リストの path に書く値。`--skills-dir` に渡すのと同じ書き方である。"""
    return (skills_dir / rel).as_posix()


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


# --- T1: 検知する ---


def test_detects_assumption_in_listed_skill(tmp_path: Path) -> None:
    """公開する Skill の本文にヒット語があれば終了コード 1 で終わる。"""
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    out = output_of(result)
    assert f"{LISTED_SKILL}/SKILL.md" in out
    assert "claude plugin " in out
    assert ":3:" in out or ":3 " in out, f"行番号が出ていない: {out}"


def test_clean_tree_passes(tmp_path: Path) -> None:
    """ヒット語が無ければ終了コード 0 で終わる。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def write_script(skills: Path, rel: str, body: str) -> Path:
    path = skills.parent / "scripts" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


ASSUMING_SCRIPT = 'PYTEST = "uv run --project plugins/playwright-kit/skills/x pytest"\n'


def test_detects_assumption_in_distributed_script(tmp_path: Path) -> None:
    """配布するスクリプトに埋め込んだ ai-plugins の形を落とす。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    write_script(skills, "supervise.py", ASSUMING_SCRIPT + 'BASE = "origin/develop"\n')
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    out = output_of(result)
    assert "scripts/supervise.py:1:" in out and "scripts/supervise.py:2:" in out


def test_script_tests_and_experimental_are_not_scanned(tmp_path: Path) -> None:
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    write_script(skills, "tests/test_x.py", ASSUMING_SCRIPT)
    write_script(skills, "experimental/x.py", ASSUMING_SCRIPT)
    write_script(skills, "notes.txt", ASSUMING_SCRIPT)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_excluded_script_passes(tmp_path: Path) -> None:
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    path = write_script(skills, "lib/release.sh", ASSUMING_SCRIPT)
    rows = [allow_row(path.as_posix(), name) for name in ("playwright-kit", "pytest")]
    result = run_check(skills, rows, tmp_path)
    assert result.returncode == 0, output_of(result)


# --- T2: 例外リストが効く ---


def test_excluded_file_passes(tmp_path: Path) -> None:
    """同じ本文でも、例外リストに載せたファイルなら終了コード 0 で終わる。"""
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    rows = [allow_row(key_for(skills, f"{LISTED_SKILL}/SKILL.md"), "claude-plugin-cmd", reason="この文書の主題は NDF 自身である")]
    result = run_check(skills, rows, tmp_path)
    assert result.returncode == 0, output_of(result)


# --- T3: 例外リストの理由が要る ---


@pytest.mark.parametrize("reason", ["", "   "])
def test_exclusion_without_reason_fails_the_check(tmp_path: Path, reason: str) -> None:
    """理由の無い例外リストは、ヒットの有無に関わらずチェック自体を失敗させる。"""
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    rows = [allow_row(key_for(skills, f"{LISTED_SKILL}/SKILL.md"), "claude-plugin-cmd", reason=reason)]
    result = run_check(skills, rows, tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "理由" in output_of(result)


# --- T4: 例外リストの陳腐化を検知する ---


def test_exclusion_pointing_at_missing_file_fails_the_check(tmp_path: Path) -> None:
    """実在しないファイルを例外リストに載せると、チェック自体が失敗する。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, [allow_row(key_for(skills, "charlie/SKILL.md"), "claude-plugin-cmd")], tmp_path)
    assert result.returncode == 2, output_of(result)
    out = output_of(result)
    assert "charlie/SKILL.md" in out


def test_exclusion_outside_scan_scope_fails_the_check(tmp_path: Path) -> None:
    """実在しても走査の範囲外なら、例外リストとして成立しないので失敗させる。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, [allow_row(key_for(skills, f"{UNLISTED_SKILL}/SKILL.md"), "claude-plugin-cmd")], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert f"{UNLISTED_SKILL}/SKILL.md" in output_of(result)


def test_exclusion_of_another_family_is_not_validated(tmp_path: Path) -> None:
    """チェックしていない plugin family の例外リストは、実在のチェックに掛けない。

    `--skills-dir` は family を 1 つだけ指定できる。走査していないものを「実在しない」と
    読むと、正しい宣言のままチェックが落ちる。
    """
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    other = tmp_path / "plugins" / "other" / "skills"
    result = run_check(skills, [allow_row(key_for(other, "delta/SKILL.md"), "claude-plugin-cmd")], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_real_exclusions_do_not_break_a_single_family_scan() -> None:
    """チェックが持つ既定の例外リストのまま、NDF 以外の family だけをチェックしても通る。"""
    result = run_check(REPO_ROOT / "plugins/playwright-kit/skills")
    assert result.returncode == 0, output_of(result)


def test_default_scan_rejects_an_exclusion_outside_every_family(tmp_path: Path) -> None:
    """family をすべて見る走査では、どの family にも属さない宣言を陳腐化として落とす。"""
    result = run_check(None, [allow_row("plugins/gone/skills/delta/SKILL.md", "claude-plugin-cmd")], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "plugins/gone/skills/delta/SKILL.md" in output_of(result)


# --- T5: 走査の範囲 ---


def test_unlisted_skill_is_not_scanned(tmp_path: Path) -> None:
    """manifest に載らない Skill の本文は走査しない。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=ASSUMING_BODY)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_tests_directory_is_not_scanned(tmp_path: Path) -> None:
    """`tests/` の下は走査しない（`build_tree` がヒット語を置いている）。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)
    assert "tests/fixture.md" not in output_of(result)


# --- T6: 実リポジトリで通る ---


def test_real_repository_passes() -> None:
    """実物の Skill でも通る。チェックを入れた時点で落ちる状態を作らない。"""
    result = run_check(REPO_ROOT / "plugins/ndf/skills")
    assert result.returncode == 0, output_of(result)


def test_default_scan_covers_the_real_repository() -> None:
    """`--skills-dir` を省いても、実物の Skill を走査して通る。"""
    result = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, output_of(result)


def test_report_shows_scan_size() -> None:
    """`--report` が走査した本数とヒット数を出す（受け入れ条件 A9 の根拠）。

    **数を直書きしない。** 走査の対象は Skill が増減するたびに変わるため、直書きした数は
    書いた時点でしか正しくない。**しかも部分一致では、指摘の一覧に出る行番号へ偶然当たる。**
    実測では `33` が `official-skills-autoloader/SKILL.md:33` に、`89` が
    `form-package-plugin.md:89` に一致しており、実際の本数（41 個 / 112 本）と食い違ったまま
    通り続けていた。**行を足して行番号が動いた時点で初めて落ちる。**

    見るのは形である。**数そのものではなく、数が出ていることを確かめる。**
    """
    result = subprocess.run([sys.executable, str(CHECKER), "--report"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, output_of(result)
    out = output_of(result)
    summary = re.search(
        r"^plugins/ndf/skills: 公開する Skill (\d+) 個 / Markdown (\d+) 本 / スクリプト (\d+) 本（family (\d+)・Skill (\d+)） / hook (\d+) 本 / ヒット (\d+) 件$",
        out,
        re.MULTILINE,
    )
    assert summary is not None, f"走査の要約が出ていない: {out}"
    skills, markdown, scripts, family_scripts, skill_scripts, hooks, _hits = (int(value) for value in summary.groups())
    assert skills > 0, f"公開する Skill の数が 0 になっている: {out}"
    assert family_scripts > 0 and skill_scripts > 0, f"配布するスクリプトを走査していない: {out}"
    assert scripts == family_scripts + skill_scripts, out
    assert hooks > 0, f"hook の定義を走査していない: {out}"
    assert markdown >= skills, f"Markdown の本数が Skill の数を下回っている: {out}"


def test_report_still_fails_on_a_hit(tmp_path: Path) -> None:
    """`--report` は出力を足すだけで、例外リストの外にヒットがあれば 1 を返す。

    レポートを常に 0 で返すと、README がこのコマンドをチェックの一覧として載せている以上、
    利用者と自動処理が違反を成功として扱う。
    """
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--skills-dir", str(skills), "--report"], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 1, output_of(result)
    # 判定を足しても、レポートの出力そのものは残る。
    assert "[検知]" in result.stdout, output_of(result)


# --- T7: manifest に載る Skill を走査できないとチェックが成立しない ---


def rewrite_manifest(skills_dir: Path, names: list[str]) -> None:
    """テスト用の木の manifest を書き直す。"""
    manifest = skills_dir.parent / "manifests" / "claude-skills.txt"
    manifest.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")


def test_missing_skill_directory_fails_the_check(tmp_path: Path) -> None:
    """manifest に載る Skill のディレクトリが無いと、チェック自体が失敗する。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    rewrite_manifest(skills, [LISTED_SKILL, "delta"])
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "delta" in output_of(result)


def test_skill_directory_without_scannable_markdown_fails_the_check(tmp_path: Path) -> None:
    """ディレクトリがあっても走査できる本文が無ければ、同じくチェック自体が失敗する。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)
    rewrite_manifest(skills, [LISTED_SKILL, "echo"])
    (skills / "echo" / "tests").mkdir(parents=True)
    (skills / "echo" / "tests" / "fixture.md").write_text(CLEAN_BODY, encoding="utf-8")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "echo" in output_of(result)


def test_unscanned_skill_outranks_a_hit(tmp_path: Path) -> None:
    """ヒットがあっても、走査の範囲が欠けていれば 1 ではなく 2 で終わる。

    範囲が欠けた結果は、ヒットの有無そのものの根拠にならない。
    """
    skills = build_tree(tmp_path, listed=ASSUMING_BODY, unlisted=CLEAN_BODY)
    rewrite_manifest(skills, [LISTED_SKILL, "delta"])
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 2, output_of(result)


# --- 走査の範囲: Skill の scripts/ と hook の定義 ---


WORD_LINE = 'BASE = "main"\n'


def write_skill_script(skills: Path, skill: str, rel: str, body: str = WORD_LINE) -> Path:
    path = skills / skill / "scripts" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def clean_tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path, listed=CLEAN_BODY, unlisted=CLEAN_BODY)


def test_detects_word_in_script_of_listed_skill(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    write_skill_script(skills, LISTED_SKILL, "lib/x.py")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert f"{LISTED_SKILL}/scripts/lib/x.py:1:" in output_of(result)


@pytest.mark.parametrize(
    ("skill", "rel"),
    [(UNLISTED_SKILL, "x.py"), (LISTED_SKILL, "tests/test_x.py"), (LISTED_SKILL, "experimental/x.py"), (LISTED_SKILL, "x.txt")],
)
def test_out_of_scope_skill_scripts_are_not_scanned(tmp_path: Path, skill: str, rel: str) -> None:
    skills = clean_tree(tmp_path)
    write_skill_script(skills, skill, rel)
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_detects_word_in_hook_definition(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    hooks = skills.parent / "hooks"
    hooks.mkdir()
    (hooks / "claude.json").write_text('{"command": "git diff origin/develop"}\n', encoding="utf-8")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert "hooks/claude.json:1:" in output_of(result)
    row = allow_row((hooks / "claude.json").as_posix(), "origin-develop")
    assert run_check(skills, [row], tmp_path).returncode == 0


# --- 検知する語 ---


@pytest.mark.parametrize(
    ("word_id", "line"),
    [
        ("quoted-main", 'BASE = "main"'),
        ("quoted-main", "BASE = 'main'"),
        ("quoted-develop", 'BASE = "develop"'),
        ("base-main", "gh pr create --base main"),
        ("base-main", "gh pr create --base=main"),
        ("admin-merge", "gh pr merge --admin"),
        ("tag-form", 'TAG = f"{plugin}--v{version}"'),
        ("release-branch", 'HEAD = f"release/v{version}"'),
        ("plugin-manifest", "open('.claude-plugin/plugin.json')"),
        ("spec-dir", 'DIR = "docs/specifications"'),
        ("pytest", 'CMD = "pytest -q"'),
    ],
)
def test_each_word_fails_a_new_file(tmp_path: Path, word_id: str, line: str) -> None:
    skills = clean_tree(tmp_path)
    write_script(skills, "new_file.py", line + "\n")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert "scripts/new_file.py:1:" in output_of(result)
    path = (skills.parent / "scripts" / "new_file.py").as_posix()
    assert run_check(skills, [allow_row(path, word_id)], tmp_path).returncode == 0


@pytest.mark.parametrize(
    "line",
    [
        'if __name__ == "__main__":',
        "def main() -> int:",
        'NAME = "domain"',
        "gh pr create --base mainline",
        'ARGS = ["--verbose", "--version", "--view"]',
        "SKILL = 'release/verification'",
        "PYTEST_ADDOPTS = 1",
        "pytest_x = 1",
    ],
)
def test_near_words_do_not_hit(tmp_path: Path, line: str) -> None:
    skills = clean_tree(tmp_path)
    write_script(skills, "new_file.py", line + "\n")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_two_words_on_one_line_are_counted_separately(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", 'A = ("main", "develop")\n').as_posix()
    result = run_check(skills, [allow_row(path, "quoted-main")], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert "'\"develop\"'" in output_of(result)
    assert run_check(skills, [allow_row(path, "quoted-main"), allow_row(path, "quoted-develop")], tmp_path).returncode == 0


# --- 当たりの数 ---


def test_new_hit_beyond_the_declared_count_fails(tmp_path: Path) -> None:
    """宣言のある組の新しい箇所も落とす。どの行が新しいかを決められないので組の当たりをすべて出す。"""
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", 'A = "main"\nB = 1\nC = "main"\n').as_posix()
    result = run_check(skills, [allow_row(path, "quoted-main")], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert "scripts/x.py:1:" in output_of(result) and "scripts/x.py:3:" in output_of(result)


def test_same_word_twice_on_a_line_counts_as_two(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", 'A = ("main", "main")\n').as_posix()
    assert run_check(skills, [allow_row(path, "quoted-main")], tmp_path).returncode == 1
    assert run_check(skills, [allow_row(path, "quoted-main", count=2)], tmp_path).returncode == 0


def test_fewer_hits_than_declared_fail_with_the_value_to_write(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", 'A = "main"\n').as_posix()
    result = run_check(skills, [allow_row(path, "quoted-main", count=3)], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "count を 1 へ直す" in output_of(result)


def test_declared_pair_without_hits_fails(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", "A = 1\n").as_posix()
    result = run_check(skills, [allow_row(path, "quoted-main")], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "項目を消す" in output_of(result)


def test_shortage_outranks_a_new_hit(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    path = write_script(skills, "x.py", 'A = "develop"\n').as_posix()
    result = run_check(skills, [allow_row(path, "quoted-main")], tmp_path)
    assert result.returncode == 2, output_of(result)


# --- 例外リストの形 ---


def script_row(skills: Path, **kw) -> dict:
    path = write_script(skills, "x.py", 'A = "main"\n').as_posix()
    return allow_row(path, kw.pop("name", "quoted-main"), **kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "pending"},
        {"count": 0},
        {"count": True},
        {"count": "1"},
        {"name": "no-such-word"},
        {"kind": "other"},
        {"issue": 0},
        {"issue": "2624"},
    ],
    ids=str,
)
def test_malformed_row_fails_the_check(tmp_path: Path, kw: dict) -> None:
    skills = clean_tree(tmp_path)
    result = run_check(skills, [script_row(skills, **kw)], tmp_path)
    assert result.returncode == 2, output_of(result)


def test_issue_on_a_non_violation_row_fails_the_check(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    row = script_row(skills) | {"issue": 2624}
    assert run_check(skills, [row], tmp_path).returncode == 2


def test_pending_row_with_issue_passes(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    assert run_check(skills, [script_row(skills, issue=2624)], tmp_path).returncode == 0


def test_two_rows_for_one_pair_fail_the_check(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    result = run_check(skills, [script_row(skills), script_row(skills, issue=2624)], tmp_path)
    assert result.returncode == 2, output_of(result)


def test_allow_path_that_is_a_file_fails_the_check(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    not_a_dir = tmp_path / "allow.json"
    not_a_dir.write_text("{}", encoding="utf-8")
    argv = [sys.executable, str(CHECKER), "--skills-dir", str(skills), "--allow", str(not_a_dir)]
    assert subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT).returncode == 2


# --- 実物の例外リスト ---


REAL_ALLOW = REPO_ROOT / "scripts/repo-assumptions-allow"


@pytest.mark.parametrize(
    "path",
    ["plugins/ndf/scripts/release-steps.py", "plugins/ndf/scripts/lib/step_result.py", "plugins/ndf/scripts/release-verification-steps.py"],
)
def test_release_scripts_have_no_whole_file_exclusion(tmp_path: Path, path: str) -> None:
    """リリースの形の 3 本も語と数で宣言する。実物の数を 1 つ減らすと、1 件足したのと同じく落ちる。"""
    rows = [json.loads(f.read_text(encoding="utf-8")) for f in REAL_ALLOW.glob("*.json")]
    target = next(r for r in rows if r["path"] == path)
    rows.remove(target)
    if target["count"] > 1:
        rows.append(target | {"count": target["count"] - 1})
    result = run_check(REPO_ROOT / "plugins/ndf/skills", rows, tmp_path)
    assert result.returncode == 1, output_of(result)
    assert path in output_of(result)
