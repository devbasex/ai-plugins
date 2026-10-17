"""release-steps.py notes: 後の PR の `## 置き換える箇条` が前の PR の箇条を CHANGELOG と配る中身から除く（#1834）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from release_lib import replaced  # noqa: E402
from test_release_steps import PY, SCRIPT, git, notes_repo  # noqa: E402

OLD = "- init は Draft でない場合に処理を止める（#1821）"
BODIES = {
    25: {"title": "スプリント", "body": f"## 利用者向けの変化\n\n{OLD}\n- 別の変化（#1822）\n"},
    27: {
        "title": "後の PR",
        "body": "## 利用者向けの変化\n\n- init は Draft かどうかを問わない\n\n"
        "## 置き換える箇条\n\n- #25: init は Draft でない場合に処理を止める\n",
    },
}
APPROVAL = (
    "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 含む PR | #25 a<br>#27 b |\n"
    "| 配る中身 | （未記入） |\n| 検証への配布で確かめたこと | （未記入） |\n\n## 同意を求めること\n\n- [ ] x\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".ndf").mkdir()
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    (root / ".ndf" / "supervise.json").write_text(json.dumps({"release": {"form": "package-plugin", "plugin": "ndf"}}))
    notes_repo(root)
    return root


def fake_gh(tmp_path: Path, prs: dict) -> tuple[dict, Path]:
    """gh pr view N に prs[N] を返し、呼ばれた引数を 1 行ずつ log へ書く偽の gh。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    data, log = tmp_path / "prs.json", tmp_path / "gh.log"
    data.write_text(json.dumps({str(k): v for k, v in prs.items()}, ensure_ascii=False), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{PY}\nimport json, sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"print(json.dumps(json.load(open({str(data)!r}))[sys.argv[3]]))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def notes(repo: Path, env: dict, *extra: str) -> dict:
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.2", "--prs", "25", "27", *extra],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def section(repo: Path) -> str:
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    return text.split("## [ndf 1.2.3]", 1)[1].split("## [ndf 1.2.2]", 1)[0]


def approval_cell(repo: Path) -> str:
    path = repo / "issues" / "approval.md"
    return next(line for line in path.read_text(encoding="utf-8").split("\n") if line.startswith("| 配る中身 |"))


def test_replaced_item_leaves_changelog_and_approval_without_editing_the_old_pr(repo, tmp_path):
    """受け入れ条件 4・5: 置き換えた箇条は CHANGELOG と配る中身の両方から消え、後の PR の箇条が並ぶ。
    前の PR の箇条の末尾が別の PR の `（#N）` でも外れ、gh pr edit は 1 回も呼ばれない。"""
    env, log = fake_gh(tmp_path, BODIES)
    res = notes(repo, env)
    body = section(repo)
    assert "Draft でない" not in body and "- init は Draft かどうかを問わない（#27）" in body and "- 別の変化（#1822）" in body
    assert res["metrics"]["replaced"] == 1
    assert {"kind": "replacement", "name": "#27→#25", "result": "removed", "text": "init は Draft でない場合に処理を止める"} in res["items"]
    (repo / "issues").mkdir()
    (repo / "issues" / "approval.md").write_text(APPROVAL, encoding="utf-8")
    notes(repo, env, "--approval", "issues/approval.md")
    cell = approval_cell(repo)
    assert "Draft でない" not in cell and "Draft かどうかを問わない（#27）" in cell
    assert not [line for line in log.read_text().splitlines() if not line.startswith(("pr view ", "api graphql "))]


def test_unmatched_replacement_removes_nothing_and_is_reported(repo, tmp_path):
    """受け入れ条件 9: 一致しない置き換え・版の外の PR を指す置き換えは何も除かず not_found で載る。終了コードは 0。"""
    bodies = {
        25: BODIES[25],
        27: {"title": "後", "body": "## 利用者向けの変化\n\n- 新\n\n## 置き換える箇条\n\n- #25: init は Draft\n- #99: 別\n- 形の違う行\n"},
    }
    env, _ = fake_gh(tmp_path, bodies)
    res = notes(repo, env)
    assert OLD in section(repo) and res["metrics"]["replaced"] == 0
    rows = [(i["name"], i["result"]) for i in res["items"] if i["kind"] == "replacement"]
    assert rows == [("#27→#25", "not_found"), ("#27→#99", "not_found"), ("#27", "unreadable")]


def test_without_replacements_the_output_is_unchanged(repo, tmp_path):
    """受け入れ条件 10: 置き換えの節が無い版の CHANGELOG の版の節は、箇条をそのまま並べたもの（括弧を外さない）。"""
    env, _ = fake_gh(tmp_path, {25: BODIES[25], 27: {"title": "後", "body": "## 利用者向けの変化\n\n- 新しい箇条\n"}})
    res = notes(repo, env)
    want = f"{OLD}（#25）\n- 別の変化（#1822）（#25）\n- 新しい箇条（#27）\n"
    assert section(repo) == f" - 2026-01-01\n\n{want}\n"
    assert res["metrics"]["replaced"] == 0 and not [i for i in res["items"] if i["kind"] == "replacement"]


def test_risks_keep_hand_lines_and_add_only_new_items(repo, tmp_path):
    """I6: 残る危険の既にある行は消さず、PR の箇条のうち無いものだけを足す。足したら「記載が無い」の行を外す。"""
    bodies = {
        25: {"title": "a", "body": "## 利用者向けの変化\n\n- x\n"},
        27: {"title": "b", "body": "## 利用者向けの変化\n\n- y\n\n## 未検証・残る危険\n\n- 実機で未確認\n"},
    }
    env, _ = fake_gh(tmp_path, bodies)
    (repo / "issues").mkdir()
    path = repo / "issues" / "approval.md"
    head, tail = APPROVAL.split("## 同意を求めること")
    path.write_text(
        f"{head}## 未検証・残る危険\n\n- PR の本文に記載が無い\n- 手で書いた危険\n\n## 同意を求めること{tail}", encoding="utf-8"
    )
    for _ in range(2):
        notes(repo, env, "--approval", "issues/approval.md")
    text = path.read_text(encoding="utf-8")
    assert "## 未検証・残る危険\n\n- 手で書いた危険\n- 実機で未確認（#27）\n\n## 移行の手順" in text


def test_comparable_drops_trailing_pr_references():
    """比べる形: 1 行目だけ・末尾の（#N）/(#N) を外す・空白をまとめる。"""
    assert replaced.comparable("a  b（#1821）（#1825）\n  - 子") == "a b"
    assert replaced.comparable("a (#3, #4)") == "a"
