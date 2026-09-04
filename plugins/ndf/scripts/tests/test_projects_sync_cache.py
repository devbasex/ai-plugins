"""盤面への記録が、全件の取得を繰り返さないこと（#243 / #287）。

`gh project item-list --limit 1000` は GraphQL で、取得の点数が REST とは別の上限を持つ。
2026-09-04 の実測では、10 件の課題へ 2 つのキーを書こうとした時点で上限に達し、以後の
記録がすべて捨てられた（終了コードは 0 のまま、出力も無い）。

**アイテムが無いことと、読めないことを区別する。** 前者は載せてから読み直し、後者は
知らせて抜ける。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "projects-sync.sh"

ITEM = {"id": "PVTI_x", "content": {"number": 42, "repository": "acme/demo"}}
FIELDS = {"fields": [
    {"id": "F_stage", "name": "進行",
     "options": [{"id": "O_design", "name": "設計"}]},
]}


@pytest.fixture()
def repo(tmp_path):
    """宣言を持つ git リポジトリと、呼び出しを記録する `gh` を用意する。"""
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "projects.json").write_text(
        json.dumps({"version": 1, "owner": "acme", "number": 1}), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    items = tmp_path / "items.json"
    items.write_text(json.dumps({"items": [ITEM], "totalCount": 1}), encoding="utf-8")
    (bin_dir / "gh").write_text(f"""#!/usr/bin/env bash
echo "$@" >> {calls}
case "$1 $2" in
  "project view") echo '{{"id":"PVT_x"}}' ;;
  "project item-list") cat {items} ;;
  "project field-list") echo '{json.dumps(FIELDS)}' ;;
  "repo view") echo "acme/demo" ;;
  "issue view") echo "https://github.com/acme/demo/issues/42" ;;
  "project item-add") echo "PVTI_new" ;;
  "project item-edit") ;;
esac
exit 0
""", encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    return type("R", (), {"root": root, "bin": bin_dir, "calls": calls, "items": items})


def run(repo):
    env = {**os.environ, "PATH": f"{repo.bin}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(SCRIPT), "42", "stage", "設計"],
        cwd=repo.root, capture_output=True, text=True, env=env, timeout=60,
    )


def test_the_second_call_does_not_list_the_board(repo):
    """2 回目の記録では盤面の全件を読まない。**上限に達する原因を断つ。**"""
    assert run(repo).returncode == 0
    first = repo.calls.read_text(encoding="utf-8")
    assert "project item-list" in first

    repo.calls.write_text("", encoding="utf-8")
    assert run(repo).returncode == 0
    second = repo.calls.read_text(encoding="utf-8")
    assert "project item-list" not in second
    assert "project view" not in second
    assert "project item-edit" in second


def test_a_missing_item_is_added_and_then_read_again(repo):
    """アイテムが無ければ載せてから読み直す。"""
    repo.items.write_text(json.dumps({"items": [], "totalCount": 0}), encoding="utf-8")

    # 追加の後に読み直すと見つかる状態を作る。
    (repo.bin / "gh").write_text((repo.bin / "gh").read_text(encoding="utf-8").replace(
        '"project item-add") echo "PVTI_new" ;;',
        f'"project item-add") echo "PVTI_new"; echo \'{json.dumps({"items": [ITEM], "totalCount": 1})}\' > {repo.items} ;;'
    ), encoding="utf-8")

    out = run(repo)
    assert out.returncode == 0
    assert "project item-add" in repo.calls.read_text(encoding="utf-8")
    assert "盤面へ追加しました" in out.stderr


def test_a_rate_limited_reply_is_reported(repo):
    """上限に達したときは黙って抜けずに 1 行知らせる。

    `gh` は所有者の種別を GraphQL で問い合わせるため、上限に達すると
    `unknown owner type` を返す。**種別の問題と区別できない**ので、上限を先に疑う。
    """
    (repo.bin / "gh").write_text("""#!/usr/bin/env bash
echo "unknown owner type" >&2
exit 1
""", encoding="utf-8")
    (repo.bin / "gh").chmod(0o755)

    out = run(repo)
    assert out.returncode == 0
    assert "上限に達している可能性" in out.stderr


def test_the_cache_lives_outside_the_worktree(tmp_path, repo):
    """控えは共通の git ディレクトリの下に置く。

    **作業ツリーでは `.git` がファイルである**ため、`.git/ndf/` を作ろうとすると失敗する。
    """
    run(repo)
    cache = list((repo.root / ".git" / "ndf").glob("projects-*.env"))
    assert cache, "控えが作られていない"
    body = cache[0].read_text(encoding="utf-8")
    assert "project_id=PVT_x" in body
    assert "item_id=PVTI_x" in body


def test_the_id_from_item_add_is_used_without_listing_again(repo):
    """追加の戻り値の識別子をそのまま使い、盤面を読み直さない。

    追加した直後の読み直しは、この変更が減らそうとした問い合わせである。索引の反映が
    遅れていると、追加したばかりのアイテムが見つからずに記録が飛ぶ余地も残る。
    """
    repo.items.write_text(json.dumps({"items": [], "totalCount": 0}), encoding="utf-8")
    out = run(repo)
    assert out.returncode == 0
    calls = repo.calls.read_text(encoding="utf-8").splitlines()
    listed = [c for c in calls if c.startswith("project item-list")]
    # 1 回目（無いことの確認）だけで、追加の後には読み直さない
    assert len(listed) == 1, calls
    assert any(c.startswith("project item-edit") for c in calls), calls
