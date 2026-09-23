"""`issue-upkeep` とつながる Skill の、構造改善で守る契約（#717 の構造改善）。

NOTE: 現状固定。期待値の根拠は仕様ではなく、構造改善の直前の手順が返す結果である。
      節の分け方や言い回しを変えても、ここに書いた分岐と値は変わらないことを確かめる。

| Skill | 固定するもの |
| --- | --- |
| `retrospective` | Pull Request の番号を引く 3 段（開発の起点 → 基準ブランチ → マージ済みの Pull Request） |
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

import pytest

SKILLS = pathlib.Path(__file__).resolve().parents[2]
RETROSPECTIVE = SKILLS / "retrospective" / "SKILL.md"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。囲みの中の `#` は見出しとして数えない。"""
    depth = len(heading.split(" ", 1)[0])
    lines = text.split("\n")
    start = lines.index(heading)
    fenced = False
    for end in range(start + 1, len(lines)):
        line = lines[end]
        if line.startswith("```"):
            fenced = not fenced
            continue
        match = re.match(r"^(#+) ", line)
        if not fenced and match and len(match.group(1)) <= depth:
            return "\n".join(lines[start:end])
    return "\n".join(lines[start:])


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
    expression = re.search(r"--jq '([^']*)'", block).group(1)
    pulls = [
        {"number": 1, "merged_at": None, "base": {"ref": "main"}, "head": {"ref": "topic"}},
        {"number": 2, "merged_at": "2026-09-01T00:00:00Z",
         "base": {"ref": "main"}, "head": {"ref": "develop"}},
    ]
    done = subprocess.run(["jq", "-r", expression], input=json.dumps(pulls),
                          capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["#2 main <- develop"]
