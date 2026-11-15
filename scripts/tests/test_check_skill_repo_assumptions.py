"""公開する Skill とスクリプトに自リポジトリ前提が混入していないかのチェックを確かめる。

記号（T1〜T6）は `issues/parallel-batch-07/06-issue-292.md` の「テスト設計」に対応する。

チェックそのものは `scripts/check-skill-repo-assumptions.py` にある。実物の Skill は
書き換えず、一時ディレクトリへ最小の木を作ってそこをチェックさせる（T6 だけが実物を読む）。
"""

from __future__ import annotations

import json
import re
import shutil
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


# --- T6: 走査の量の報告（実リポジトリの検査は suite skill-repo-assumptions-check が流す） ---


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
        r"^plugins/ndf/skills: 公開する Skill (\d+) 個 / Markdown (\d+) 本 / スクリプト (\d+) 本（family (\d+)・Skill (\d+)） / hook (\d+) 本 / エージェント (\d+) 本 / ヒット (\d+) 件$",
        out,
        re.MULTILINE,
    )
    assert summary is not None, f"走査の要約が出ていない: {out}"
    skills, markdown, scripts, family_scripts, skill_scripts, hooks, agents, _hits = (int(value) for value in summary.groups())
    assert skills > 0, f"公開する Skill の数が 0 になっている: {out}"
    assert family_scripts > 0 and skill_scripts > 0, f"配布するスクリプトを走査していない: {out}"
    assert scripts == family_scripts + skill_scripts, out
    assert hooks > 0, f"hook の定義を走査していない: {out}"
    assert agents > 0, f"エージェントの本文を走査していない: {out}"
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
        ("quoted-main", 'BASE = "master"'),
        ("quoted-main", "BASE = 'master'"),
        ("quoted-develop", 'BASE = "develop"'),
        ("origin-main", 'BASE = "origin/main"'),
        ("origin-main", "git diff origin/master"),
        ("base-main", "gh pr create --base main"),
        ("base-main", "gh pr create --base master"),
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


@pytest.mark.parametrize(
    "line",
    ["for c in main master; do", "git checkout develop", "BASE=main", "git push origin master || true"],
)
def test_shell_word_fails_a_new_shell_script(tmp_path: Path, line: str) -> None:
    """#3309: 引用符の無いシェルの単語として書いたブランチ名を拾う。"""
    skills = clean_tree(tmp_path)
    path = write_script(skills, "new_file.sh", line + "\n")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    assert "scripts/new_file.sh:1:" in output_of(result)
    count = len(re.findall(r"\b(?:main|master|develop)\b", line))
    assert run_check(skills, [allow_row(path.as_posix(), "shell-branch-word", count=count)], tmp_path).returncode == 0


@pytest.mark.parametrize(
    "line",
    ["# main / master の順で探す", "main() { :; }", "wt_main_dir=1", 'echo "$domain"', "./maintain.sh"],
)
def test_near_shell_words_do_not_hit(tmp_path: Path, line: str) -> None:
    skills = clean_tree(tmp_path)
    write_script(skills, "new_file.sh", line + "\n")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 0, output_of(result)


def test_shell_words_are_not_applied_to_python(tmp_path: Path) -> None:
    skills = clean_tree(tmp_path)
    write_script(skills, "new_file.py", "for c in main, master:\n    pass\n")
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


# --- 現状固定テスト（I-003: scan_family の戻り値） ---------------------------------------------------


def load_checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_skill_repo_assumptions_i003", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    # dataclass は遅延評価の型注釈を解くときに sys.modules から自分のモジュールを引く
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FAMILY_FILES = {
    "manifests/claude-skills.txt": "alpha  # c\nghost\n",
    "manifests/codex-skills.txt": "bravo\n",
    "skills/alpha/SKILL.md": "# t\nrun `claude plugin validate` on develop\n",
    "skills/alpha/references/r.md": "origin/main\n",
    "skills/alpha/tests/t.md": "develop\n",
    "skills/alpha/scripts/x.sh": '# main\ngit checkout develop\nB="main"\n',
    "skills/alpha/scripts/y.py": "import pytest\n",
    "skills/alpha/scripts/experimental/z.py": "pytest\n",
    "skills/bravo/SKILL.md": "clean\n",
    "skills/charlie/SKILL.md": "develop\n",
    "scripts/a.py": 'B = "main"  # plugins/ndf/\n',
    "scripts/b.sh": "for c in main master; do :; done\n",
    "scripts/tests/t.py": "pytest\n",
    "scripts/c.txt": "pytest\n",
    "hooks/claude.json": '{"c":"git diff origin/develop"}\n',
    "hooks/x.txt": "origin/develop\n",
    "agents/ag.md": "use --base main\n",
}


def build_family(tmp_path: Path) -> Path:
    """本文・Skill のスクリプト・family のスクリプト・hook・エージェントをすべて持つ family。`skills/` を返す。"""
    family = tmp_path / "plugins" / "fx"
    for rel, body in FAMILY_FILES.items():
        path = family / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return family / "skills"


def test_characterize_scan_family_returns_scanned_hits_report_and_unscanned(tmp_path: Path) -> None:
    mod = load_checker()
    skills = build_family(tmp_path)
    family = skills.parent
    got = mod.scan_family(skills)
    scanned, hits, (report_line,), unscanned, problems = got.scanned, got.hits, got.report_lines, got.unscanned, got.default_problems
    assert problems == []

    assert sorted(Path(p).relative_to(family.resolve()).as_posix() for p in scanned) == [
        "agents/ag.md",
        "hooks/claude.json",
        "scripts/a.py",
        "scripts/b.sh",
        "skills/alpha/SKILL.md",
        "skills/alpha/references/r.md",
        "skills/alpha/scripts/x.sh",
        "skills/alpha/scripts/y.py",
        "skills/bravo/SKILL.md",
    ]
    # 並びも固定する（本文 → family のスクリプト → Skill のスクリプト → シェルの語 → hook → エージェント）
    assert [(h.skills_dir.relative_to(family).as_posix(), h.rel, h.lineno, h.word_id, h.word) for h in hits] == [
        ("skills", "alpha/SKILL.md", 2, "claude-plugin-cmd", "claude plugin "),
        ("skills", "alpha/SKILL.md", 2, "develop-branch", "develop"),
        ("skills", "alpha/references/r.md", 1, "origin-main", "origin/main"),
        ("scripts", "a.py", 1, "ndf-dir", "plugins/ndf/"),
        ("scripts", "a.py", 1, "quoted-main", '"main"'),
        ("skills", "alpha/scripts/x.sh", 3, "quoted-main", '"main"'),
        ("skills", "alpha/scripts/y.py", 1, "pytest", "pytest"),
        ("scripts", "b.sh", 1, "shell-branch-word", "main"),
        ("scripts", "b.sh", 1, "shell-branch-word", "master"),
        ("skills", "alpha/scripts/x.sh", 2, "shell-branch-word", "develop"),
        ("hooks", "claude.json", 1, "origin-develop", "origin/develop"),
        ("agents", "ag.md", 1, "base-branch-name", "--base main"),
    ]
    assert report_line == (
        f"{skills}: 公開する Skill 3 個 / Markdown 3 本 / スクリプト 4 本（family 2・Skill 2） / "
        "hook 1 本 / エージェント 1 本 / ヒット 12 件"
    )
    assert unscanned == [skills / "ghost"]


def test_characterize_scan_family_of_an_empty_family(tmp_path: Path) -> None:
    mod = load_checker()
    skills = tmp_path / "plugins" / "fx" / "skills"
    skills.mkdir(parents=True)
    got = mod.scan_family(skills)
    scanned, hits, (report_line,), unscanned, problems = got.scanned, got.hits, got.report_lines, got.unscanned, got.default_problems
    assert problems == []
    assert (scanned, hits, unscanned) == (set(), [], [])
    assert report_line == (
        f"{skills}: 公開する Skill 0 個 / Markdown 0 本 / スクリプト 0 本（family 0・Skill 0） / "
        "hook 0 本 / エージェント 0 本 / ヒット 0 件"
    )


# --- ndf-next の形の family（#3617）---

REMOTE_SCHEMA = '{\n  "properties": {\n    "remote": {\n      "type": "string",\n      "default": "origin"\n    }\n  }\n}\n'


def build_next_tree(tmp_path: Path, *, src: str = "X = 1\n", contract: str = '{"a": 1}\n', schema: str = REMOTE_SCHEMA) -> Path:
    """Skill の一覧を `manifests/skills.json` に持つ最小の木を作り、`skills/` を返す。"""
    family = tmp_path / "plugins" / "nx"
    (family / "manifests").mkdir(parents=True)
    (family / "manifests" / "skills.json").write_text(json.dumps({"skills": [{"name": LISTED_SKILL}]}), encoding="utf-8")
    skills = family / "skills"
    (skills / LISTED_SKILL).mkdir(parents=True)
    (skills / LISTED_SKILL / "SKILL.md").write_text(CLEAN_BODY, encoding="utf-8")
    (family / "src" / "pkg").mkdir(parents=True)
    (family / "src" / "pkg" / "a.py").write_text(src, encoding="utf-8")
    decl = family / "contracts" / "schemas" / "declarations"
    decl.mkdir(parents=True)
    (decl / "worktree.schema.json").write_text(schema, encoding="utf-8")
    (family / "contracts" / "c.json").write_text(contract, encoding="utf-8")
    return skills


def test_report_scans_ndf_next_skills_src_and_contracts() -> None:
    """ndf-next の Skill・src・contracts の走査の件数が 0 でなく、Skill の数はディレクトリの数と等しい（受け入れ条件 1）。"""
    result = subprocess.run([sys.executable, str(CHECKER), "--report"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, output_of(result)
    summary = re.search(
        r"^plugins/ndf-next/skills: 公開する Skill (\d+) 個 / Markdown (\d+) 本 / .* / src (\d+) 本 / contracts (\d+) 本 / ",
        output_of(result),
        re.MULTILINE,
    )
    assert summary is not None, output_of(result)
    skills, markdown, src, contracts = (int(v) for v in summary.groups())
    dirs = [d for d in (REPO_ROOT / "plugins/ndf-next/skills").iterdir() if d.is_dir()]
    assert skills == len(dirs) > 0 and markdown >= skills and src > 0 and contracts > 0


def test_a_new_name_in_ndf_next_src_fails_with_file_and_line(tmp_path: Path) -> None:
    """例外の外の語（`devbasex`）を src に 1 行足すと 1 で落ち、ファイルと行と足す項目の名前を出す（受け入れ条件 2・I1）。"""
    skills = build_next_tree(tmp_path, src="X = 1\nORG = 'devbasex'\n")
    result = run_check(skills, [], tmp_path)
    assert result.returncode == 1, output_of(result)
    out = output_of(result)
    assert "src/pkg/a.py:2: 'devbasex'" in out
    assert "src__pkg__a.py--org-name--pending.json" in out


def test_one_more_hit_than_declared_fails_and_an_exact_count_passes(tmp_path: Path) -> None:
    """宣言の件数どおりなら通り、1 件増えると落ちる（受け入れ条件 3・I1）。"""
    skills = build_next_tree(tmp_path, src="A = 'ai-plugins'\n")
    path = key_for(skills.parent, "src/pkg/a.py")
    assert run_check(skills, [allow_row(path, "repo-name", issue=3617)], tmp_path).returncode == 0
    (skills.parent / "src/pkg/a.py").write_text("A = 'ai-plugins'\nB = 'ai-plugins'\n", encoding="utf-8")
    assert run_check(skills, [allow_row(path, "repo-name", issue=3617)], tmp_path).returncode == 1


@pytest.mark.parametrize("row", [{"reason": ""}, {"kind": "pending", "issue": None}])
def test_a_row_without_reason_or_issue_fails(tmp_path: Path, row: dict) -> None:
    """理由の無い項目・Issue の番号の無い pending の項目で 2 で落ちる（受け入れ条件 3・I2）。"""
    skills = build_next_tree(tmp_path, src="A = 'ai-plugins'\n")
    base = allow_row(key_for(skills.parent, "src/pkg/a.py"), "repo-name")
    broken = {k: v for k, v in {**base, **row}.items() if v is not None}
    assert run_check(skills, [broken], tmp_path).returncode == 2


def test_next_words_are_not_applied_to_other_families(tmp_path: Path) -> None:
    """追加の語は ndf-next の形の family にだけ掛ける。今の形の family の件数は変わらない（受け入れ条件 4・I3）。"""
    skills = build_tree(tmp_path, listed=CLEAN_BODY + "\ndevbasex/ai-plugins の例と ndf@x\n", unlisted=CLEAN_BODY)
    assert run_check(skills, [], tmp_path).returncode == 0


def test_contract_default_is_not_a_hit_but_other_keys_are(tmp_path: Path) -> None:
    """`"default": "origin"` の値は走査しないが、ほかの鍵の値の "origin" は当たる（I7a）。"""
    mod = load_checker()
    skills = build_next_tree(tmp_path, contract='{"description": "x", "enum": ["origin"]}\n')
    root = skills.parent
    hits = mod.scan_contracts(root, ["contracts/c.json", "contracts/schemas/declarations/worktree.schema.json"], mod.NEXT_CODE_MATCHERS)
    assert [(h.rel, h.word_id) for h in hits] == [("contracts/c.json", "remote-origin")]
    assert mod.remote_defaults(root, ["contracts/schemas/declarations/worktree.schema.json"]) == [
        ("contracts/schemas/declarations/worktree.schema.json", 5)
    ]


@pytest.mark.parametrize(
    ("contract", "schema"),
    [
        ('{"default": "origin"}\n', REMOTE_SCHEMA),  # 2 か所
        ('{"a": 1}\n', '{"properties": {}}\n'),  # 0 か所
        ('{"default": "origin"}\n', '{"properties": {}}\n'),  # 別の置き場の 1 か所
    ],
)
def test_remote_default_must_be_in_exactly_one_place(tmp_path: Path, contract: str, schema: str) -> None:
    """リモート名の既定がスキーマの 1 か所でなければ 2 で落ちる（I7a）。"""
    result = run_check(build_next_tree(tmp_path, contract=contract, schema=schema), [], tmp_path)
    assert result.returncode == 2, output_of(result)
    assert "リモート名の既定が" in output_of(result)


def test_broken_skills_json_and_missing_src_fail(tmp_path: Path) -> None:
    """Skill の一覧が読めない・src が無いときは、0 件と読まずに 2 で落ちる。"""
    skills = build_next_tree(tmp_path / "a")
    (skills.parent / "manifests" / "skills.json").write_text("{", encoding="utf-8")
    assert run_check(skills, [], tmp_path / "a").returncode == 2
    skills = build_next_tree(tmp_path / "b")
    shutil.rmtree(skills.parent / "src")
    result = run_check(skills, [], tmp_path / "b")
    assert result.returncode == 2 and "走査の対象が無い" in output_of(result)


def test_ndf_next_skill_docs_get_the_name_words(tmp_path: Path) -> None:
    """ndf-next の Skill の本文には名前の語と機能の対応表の語を掛ける（受け入れ条件 16〜18）。"""
    skills = build_next_tree(tmp_path)
    (skills / LISTED_SKILL / "SKILL.md").write_text(CLEAN_BODY + "\n（ai-plugins）と[機能の対応表](x)\n", encoding="utf-8")
    out = output_of(run_check(skills, [], tmp_path))
    assert "'ai-plugins'" in out and "'機能の対応表'" in out


# --- 現状固定テスト（I-020: _split_defaults。scan_contracts と remote_defaults から通す） --------------------------


def _write_defaults_tree(tmp_path: Path) -> Path:
    files = {
        "a.json": '{\n"default": "origin", "enum": ["origin"]\n"x": 1\n"default"  :  "use origin/main", "y": "remotes/origin"\n}\n',
        "b.json": '{"default": "origin"}\n',
        "c.json": '{"k": 1}\n',
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_split_defaults_current_behavior_separates_default_values(tmp_path: Path) -> None:
    """同じ行でも `"default"` の値の中の当たりは既定の側へ、外の当たりはヒットへ分かれる。"""
    mod = load_checker()
    root = _write_defaults_tree(tmp_path)
    rels = ["a.json", "b.json", "c.json"]
    hits = mod.scan_contracts(root, rels, mod.NEXT_CODE_MATCHERS)
    assert [(h.rel, h.lineno, h.word_id, h.word) for h in hits] == [
        ("a.json", 4, "origin-main", "origin/main"),
        ("a.json", 2, "remote-origin", '"origin"'),
        ("a.json", 4, "remote-origin", "remotes/origin"),
    ]
    assert mod.remote_defaults(root, rels) == [("a.json", 2), ("a.json", 4), ("b.json", 1)]


def test_split_defaults_current_behavior_skipped_without_remote_word(tmp_path: Path) -> None:
    """語 `remote-origin` を渡さなければ既定の分け方を通らない。"""
    mod = load_checker()
    root = _write_defaults_tree(tmp_path)
    others = [(w, r) for w, r in mod.NEXT_CODE_MATCHERS if w != mod.REMOTE_WORD]
    hits = mod.scan_contracts(root, ["a.json"], others)
    assert [(h.rel, h.lineno, h.word_id, h.word) for h in hits] == [("a.json", 4, "origin-main", "origin/main")]


def test_split_defaults_current_behavior_unreadable_file_raises(tmp_path: Path) -> None:
    mod = load_checker()
    with pytest.raises(mod.ScanError, match="を読めない"):
        mod.remote_defaults(tmp_path, ["missing.json"])
