"""`issue-upkeep` とつながる 3 つの Skill の、構造改善で守る契約（#717 の構造改善）。

NOTE: 現状固定。期待値の根拠は仕様ではなく、構造改善の直前の手順が返す結果である。
      節の分け方や言い回しを変えても、ここに書いた分岐と値は変わらないことを確かめる。

| Skill | 固定するもの |
| --- | --- |
| `retrospective` | Pull Request の番号を引く 3 段（開発の起点 → 基準ブランチ → マージ済みの Pull Request） |
| `problem-solving` | 型不一致の危険な組み合わせと対策、それを指す 2 か所 |
| `out-of-scope` | 由来の形、由来を書く場所、由来での検索 |
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

import pytest

from _markdown import plain, section, table

SKILLS = pathlib.Path(__file__).resolve().parents[2]
RETROSPECTIVE = SKILLS / "retrospective" / "SKILL.md"
PROBLEM_SOLVING = SKILLS / "problem-solving" / "SKILL.md"
OUT_OF_SCOPE = SKILLS / "out-of-scope" / "SKILL.md"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def bash_blocks(text: str) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M)


# ---------- retrospective: Pull Request の番号を引く ----------

PR_SECTION = "#### Pull Request の番号を特定する"


def pr_blocks() -> dict[str, tuple[int, str]]:
    """番号を引く節の bash を、役割ごとに本文での位置と一緒に返す。"""
    blocks = bash_blocks(section(read(RETROSPECTIVE), PR_SECTION))
    found: dict[str, tuple[int, str]] = {}
    for index, block in enumerate(blocks):
        if "dev_base=$(jq" in block:
            found["dev_base"] = (index, block)
        elif re.search(r"^record_base=\$dev_base$", block, re.M):
            found["no_issue"] = (index, block)
        elif "record_base=$(git symbolic-ref" in block:
            found["group"] = (index, block)
        elif "/pulls" in block:
            found["pulls"] = (index, block)
    assert set(found) == {"dev_base", "no_issue", "group", "pulls"}, found
    return found


def git(cwd: pathlib.Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_TERMINAL_PROMPT="0")
    done = subprocess.run(["git", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture()
def clone(tmp_path: pathlib.Path) -> pathlib.Path:
    """origin の HEAD が main を指し、origin に develop もあるクローン。起点の宣言は develop。"""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "commit", "-q", "--allow-empty", "-m", "main")
    git(seed, "checkout", "-q", "-b", "develop")
    git(seed, "commit", "-q", "--allow-empty", "-m", "develop")
    git(tmp_path, "clone", "-q", "--bare", str(seed), "origin.git")
    git(tmp_path / "origin.git", "symbolic-ref", "HEAD", "refs/heads/main")
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), "work")
    work = tmp_path / "work"
    declare(work, "develop")
    return work


def declare(work: pathlib.Path, branch: str) -> None:
    (work / ".ndf").mkdir(exist_ok=True)
    (work / ".ndf" / "worktree.json").write_text(
        json.dumps({"version": 1, "base_branch": branch}), encoding="utf-8")


def run_stages(work: pathlib.Path, case: str) -> subprocess.CompletedProcess:
    """段 1 と、場合に応じた段 2 を続けて流し、2 つの変数を出す。"""
    blocks = pr_blocks()
    script = (f"set -uo pipefail\n{blocks['dev_base'][1]}\n{blocks[case][1]}\n"
              'printf "%s %s %s\\n" "$dev_base" "$record_base" '
              '"$(git rev-parse "origin/$record_base")"\n')
    return subprocess.run(["bash", "-c", script], cwd=str(work),
                          capture_output=True, text=True)


def test_retrospective_stages_run_in_order() -> None:
    """開発の起点 → 基準ブランチ → Pull Request の順に並ぶ。"""
    blocks = pr_blocks()
    assert blocks["dev_base"][0] < blocks["no_issue"][0] < blocks["pulls"][0]
    assert blocks["dev_base"][0] < blocks["group"][0] < blocks["pulls"][0]


@pytest.mark.parametrize(("case", "branch"), [
    ("no_issue", "develop"),
    ("group", "main"),
])
def test_retrospective_picks_the_branch_by_case(
        clone: pathlib.Path, case: str, branch: str) -> None:
    """起点の issue を持たない変更は開発の起点を、まとまりは配布した先（origin の HEAD）を使う。"""
    done = run_stages(clone, case)
    assert done.returncode == 0, done.stderr
    dev_base, record_base, sha = done.stdout.split()
    assert dev_base == "develop"
    assert record_base == branch
    assert sha == git(clone, "rev-parse", f"origin/{branch}")


def test_retrospective_stops_when_the_declared_branch_is_missing(clone: pathlib.Path) -> None:
    """宣言したブランチが無ければ、既定ブランチへ落とさずに止まる。"""
    declare(clone, "no-such-branch")
    for case in ("no_issue", "group"):
        done = run_stages(clone, case)
        assert done.returncode == 1, done.stdout
        assert done.stdout == ""
        assert "base_branch が指す no-such-branch" in done.stderr


def test_retrospective_group_stops_without_the_distribution_branch(clone: pathlib.Path) -> None:
    """まとまりで origin の HEAD が取れなければ、推測せずに止まる。起点の issue を持たない変更は止まらない。"""
    git(clone, "remote", "set-head", "origin", "-d")
    group = run_stages(clone, "group")
    assert group.returncode == 1, group.stdout
    assert "番号を利用者に聞いてください" in group.stderr
    assert run_stages(clone, "no_issue").returncode == 0


def test_retrospective_keeps_only_merged_pull_requests() -> None:
    """基準コミットに付く Pull Request のうち、マージ済みだけを番号として出す。"""
    block = pr_blocks()["pulls"][1]
    assert 'gh api "/repos/$RECORD_REPO/commits/$(git rev-parse "origin/$record_base")/pulls"' in block
    expression = re.search(r"--jq '([^']*)'", block).group(1)
    pulls = [
        {"number": 1, "merged_at": None, "base": {"ref": "main"}, "head": {"ref": "topic"}},
        {"number": 2, "merged_at": "2026-09-01T00:00:00Z",
         "base": {"ref": "main"}, "head": {"ref": "develop"}},
    ]
    done = subprocess.run(["jq", "-r", expression], input=json.dumps(pulls),
                          capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["#2 main <- develop"]


def test_retrospective_asks_when_not_exactly_one_pull_request() -> None:
    """絞った結果が 1 件でないときは、推測で投稿せず番号を利用者に聞く。"""
    text = plain(section(read(RETROSPECTIVE), PR_SECTION))
    assert "起点が 1 件の issue なら、番号はそのまま使える" in text
    assert "絞った結果が 1 件でないときは、推測で投稿せず番号を利用者に聞く" in text
    cases = {row[0]: row[1] for row in table(section(read(RETROSPECTIVE), PR_SECTION),
                                            "| 場合 | 起点にするコミット |")}
    assert set(cases) == {"起点の issue を持たない変更", "まとまり"}
    assert "`base_branch`" in cases["起点の issue を持たない変更"]
    assert "正式版のチャネル" in cases["まとまり"]


# ---------- problem-solving: 型不一致 ----------

TYPE_SECTION = "### 型不一致の検出パターン"


def test_problem_solving_lists_the_risky_type_pairs() -> None:
    """危険な組み合わせ 3 つと、そのリスクと対策。"""
    part = section(read(PROBLEM_SOLVING), TYPE_SECTION)
    pairs = table(part, "| ローカルキー型 | 外部キー型 | リスク |")
    assert [(row[0], row[1]) for row in pairs] == [
        ("VARCHAR", "INT"), ("INT", "VARCHAR"), ("string", "integer"),
    ]
    assert "Eager Load マッチングでずれる" in pairs[0][2]
    assert "strict comparison で不一致" in pairs[2][2]
    assert "対策: 型キャストで揃えるか、リレーション定義時に明示的に型変換する。" in plain(part)


def test_problem_solving_points_at_type_mismatch_from_two_places() -> None:
    """見逃しパターンの 2WD/4WD の行と、チェックリストの型の行が型不一致を扱う。"""
    body = read(PROBLEM_SOLVING)
    missed = {row[0]: row for row in table(body, "| 症状 | 表層の「原因」 | 真の根本原因 |")}
    assert list(missed) == ["料金が異常値", "レコードの2WD/4WD逆転", "一部ユーザーで通知が届かない"]
    cause = missed["レコードの2WD/4WD逆転"][2]
    assert "リレーション型不一致" in cause and "Eager Load" in cause
    checks = {row[0]: row[1] for row in table(body, "| チェック項目 | 方法 |")}
    assert "DB定義" in checks["型が一致するか"]
    assert "`$casts`" in checks["型が一致するか"]


def test_problem_solving_keeps_the_type_pairs_in_one_table() -> None:
    """型の組み合わせは検出パターンの表だけが持ち、2 か所はそこを指す。"""
    body = read(PROBLEM_SOLVING)
    part = section(body, TYPE_SECTION)
    outside = body.replace(part, "")
    assert not re.search(r"(?<![A-Za-z])(VARCHAR|INT|string|integer)(?![A-Za-z])", outside), "表の外に型の組み合わせがある"
    missed = {row[0]: row for row in table(body, "| 症状 | 表層の「原因」 | 真の根本原因 |")}
    checks = {row[0]: row[1] for row in table(body, "| チェック項目 | 方法 |")}
    for cell in (missed["レコードの2WD/4WD逆転"][2], checks["型が一致するか"]):
        assert "「型不一致の検出パターン」" in cell, cell


# ---------- out-of-scope: 由来 ----------

TRACE_SECTION = "## 起票した課題の辿り方"


def test_out_of_scope_origin_is_written_in_the_body_and_comments() -> None:
    """由来は起票の本文に節として残り、重複のときは既存の課題へのコメントに残る。"""
    body = read(OUT_OF_SCOPE)
    assert "## 由来\n" in body
    comment = [block for block in bash_blocks(body) if "gh issue comment" in block]
    assert comment and "<由来>" in comment[0]
    report = [block for block in bash_blocks(body) if "gh pr comment" in block]
    assert report and "#<起票した番号>" in report[0]
    assert "Pull Request がまだ無い段階では、起点の issue へ同じ 1 行を足す" in plain(body)


def test_out_of_scope_origin_has_two_forms() -> None:
    """由来は `PR #<番号>` か `issue #<番号>` で、Pull Request を作る前は issue の番号で書く。"""
    text = plain(read(OUT_OF_SCOPE))
    assert "`PR #<番号>` か `issue #<番号>`" in text
    assert "Pull Request を作る前に見つけた課題は、起点の issue の番号で書く" in text


def test_out_of_scope_origin_rule_lives_in_the_trace_section() -> None:
    """由来の形の規則は辿り方の節だけが持ち、手順 4〜6 はそこを指す。"""
    body = read(OUT_OF_SCOPE)
    trace = section(body, TRACE_SECTION)
    rule = "Pull Request を作る前に見つけた課題は、起点の issue の番号で書く"
    assert plain(body).count(rule) == 1 and rule in plain(trace)
    for step in ("### 4. 重複を確かめる", "### 5. 起票する", "### 6. 由来を残す"):
        assert "「起票した課題の辿り方」" in section(body, step), step


def test_out_of_scope_searches_by_origin_in_every_repository() -> None:
    """由来で検索し、本文とコメントの両方を対象にし、両方のリポジトリを見る。"""
    part = section(read(OUT_OF_SCOPE), TRACE_SECTION)
    search = [block for block in bash_blocks(part) if "gh issue list" in block]
    assert search and '--state all --search "<由来>"' in search[0]
    assert '"PR #177" / "issue #175"' in search[0]
    text = plain(part)
    assert "in:body で絞らない" in text.replace("`", "")
    assert "両方のリポジトリを検索する" in text
    assert "label は増やさない" in text
