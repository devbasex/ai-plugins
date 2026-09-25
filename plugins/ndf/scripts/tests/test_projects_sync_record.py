"""記録のコマンド 1 行で、issue の本文と盤面の両方へ残ること（#828）。

`projects-sync.sh` が入口である。通過工程の控えはこのコマンドを観測して積むため、入口を
変えずに issue の本文の更新（`progress-record.sh`）を中から呼ぶ（設計の決定 1）。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
SYNC = SCRIPTS / "projects-sync.sh"
RECORD = SCRIPTS / "progress-record.sh"

ITEM = {"id": "PVTI_x", "content": {"number": 42, "repository": "acme/demo"}}
FIELDS = {"fields": [
    {"id": "F_stage", "name": "進行", "options": [{"id": "O_design", "name": "設計"}]},
    {"id": "F_mode", "name": "モード", "options": [{"id": "O_std", "name": "standard"}]},
    {"id": "F_status", "name": "Status", "options": [{"id": "O_done", "name": "Done"}]},
    {"id": "F_wt", "name": "作業ツリー"},
    {"id": "F_plan", "name": "計画ファイル"},
]}
BODY = "# 課題\n\n本文\n\n## 進行\n\nモード: —\n\n- [x] 作業場所の用意 — 2026-01-01 00:00\n"


@pytest.fixture()
def repo(tmp_path):
    """git リポジトリと、issue の本文と盤面の呼び出しを記録する偽の `gh` を用意する。"""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    body = tmp_path / "body.md"
    body.write_text(BODY, encoding="utf-8")
    items = json.dumps({"items": [ITEM], "totalCount": 1})
    (bin_dir / "gh").write_text(f"""#!/usr/bin/env bash
echo "$@" >> {calls}
case "$1 $2" in
  "issue view")
    case "$*" in *"--json body"*) cat {body} ;; *) echo "https://github.com/acme/demo/issues/42" ;; esac ;;
  "issue edit")
    prev=; for a in "$@"; do [ "$prev" = --body-file ] && cp "$a" {body}; prev=$a; done ;;
  "project view") echo '{{"id":"PVT_x"}}' ;;
  "project item-list") echo '{items}' ;;
  "project field-list") echo '{json.dumps(FIELDS)}' ;;
  "repo view") echo "acme/demo" ;;
  "project item-edit") ;;
esac
exit 0
""", encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    return type("R", (), {"root": root, "bin": bin_dir, "calls": calls, "body": body})


def declare(repo):
    (repo.root / ".ndf").mkdir(exist_ok=True)
    (repo.root / ".ndf" / "projects.json").write_text(
        json.dumps({"version": 1, "owner": "acme", "number": 1}), encoding="utf-8")


def run(repo, script, *args):
    env = {**os.environ, "PATH": f"{repo.bin}:{os.environ['PATH']}", "LC_ALL": "C.UTF-8"}
    return subprocess.run(["bash", str(script), *args], cwd=repo.root,
                          capture_output=True, text=True, env=env, timeout=60)


def calls(repo):
    return repo.calls.read_text(encoding="utf-8") if repo.calls.exists() else ""


def test_without_a_board_declaration_the_issue_body_is_still_written(repo):
    """盤面の宣言が無くても issue の本文へ残る。今は何も出さずに抜けていた。"""
    out = run(repo, SYNC, "42", "stage", "設計")
    assert out.returncode == 0, out.stderr
    assert "- [x] 設計 —" in repo.body.read_text(encoding="utf-8")
    assert "#42 進行 = 設計" in out.stdout
    assert "project" not in calls(repo)


def test_with_a_board_declaration_both_are_written(repo):
    declare(repo)
    out = run(repo, SYNC, "42", "stage", "設計")
    assert out.returncode == 0, out.stderr
    assert "- [x] 設計 —" in repo.body.read_text(encoding="utf-8")
    assert "project item-edit" in calls(repo)
    # issue の本文の行と盤面の行の 2 つが出る
    assert out.stdout.count("#42 進行 = 設計") == 2


def test_mode_changes_only_the_heading_line(repo):
    """`mode` は見出し行だけを変え、チェックリストに印を足さない。"""
    out = run(repo, SYNC, "42", "mode", "standard")
    assert out.returncode == 0, out.stderr
    body = repo.body.read_text(encoding="utf-8")
    assert "モード: standard" in body
    assert body.count("- [x]") == 1
    assert "- [x] 作業場所の用意 — 2026-01-01 00:00" in body


@pytest.mark.parametrize("key,value", [("stage", "でたらめ"), ("mode", "でたらめ"), ("tier", "x")])
def test_a_wrong_value_writes_nothing(repo, key, value):
    """誤りは 2 を返し、issue の本文も盤面も書かない（宣言の有無によらない）。"""
    out = run(repo, SYNC, "42", key, value)
    assert out.returncode == 2
    assert repo.body.read_text(encoding="utf-8") == BODY
    assert calls(repo) == ""


def test_status_does_not_write_the_issue_body(repo):
    """`status` は盤面だけに書く（「ミッションを閉じる」だけが使う）。"""
    declare(repo)
    out = run(repo, SYNC, "42", "status", "Done")
    assert out.returncode == 0, out.stderr
    assert repo.body.read_text(encoding="utf-8") == BODY
    assert "issue edit" not in calls(repo)


@pytest.mark.parametrize("key,value,flag", [
    ("stage", "設計", None),
    ("mode", "standard", "--mode"),
    ("worktree", ".worktrees/feat/x", "--worktree"),
    ("plan", "issues/x.md", "--plan"),
])
def test_the_body_matches_the_two_commands_run_separately(repo, tmp_path, key, value, flag):
    """1 行で残る本文が、今の 2 コマンドの組（本文は `progress-record.sh`）と同じになる。"""
    out = run(repo, SYNC, "42", key, value)
    assert out.returncode == 0, out.stderr
    one_line = repo.body.read_text(encoding="utf-8")

    repo.body.write_text(BODY, encoding="utf-8")
    args = ["42", value] if flag is None else ["42", "-", flag, value]
    out = run(repo, RECORD, *args)
    assert out.returncode == 0, out.stderr
    separate = repo.body.read_text(encoding="utf-8")
    assert one_line == separate
    assert value in one_line


def test_missing_gh_writes_nothing(repo, tmp_path):
    """`gh` が無ければ何もせず 0 で終わる。工程を止めない。"""
    bin_dir = tmp_path / "nogh"
    bin_dir.mkdir()
    for name in ("bash", "git", "python3", "date", "mktemp", "cmp", "cat", "grep",
                 "sed", "dirname", "rm", "cp", "jq"):
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    out = subprocess.run([str(bin_dir / "bash"), str(SYNC), "42", "stage", "設計"],
                         cwd=repo.root, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PATH": str(bin_dir)})
    assert out.returncode == 0
    assert out.stdout == ""
    assert repo.body.read_text(encoding="utf-8") == BODY
