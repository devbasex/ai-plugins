"""新規 init の変更ファイル一覧が REST API から取れないとき、`gh pr view` フォールバック
経路へ落ちて `cmd_init` の state 生成と `auto_review_categories` へつながることを固定する
（`_init_new_state`、現状固定）。

`_parse_pr_files_api_lines` / `_parse_pr_files_payload` の下位パーサ単体は既存テスト
（`test_state_pr_files_parse_edges.py`）で固定されているが、`_fetch_changed_files` が
実際に REST → fallback の順で呼ばれ、その結果が `cmd_init` を通して `changed_files` /
`auto_review_categories` へ接続される経路は固定されていなかった。

worktree 作成・既存コメント取得・認証確認はこの経路の対象外のためスタブする。
`gh` だけは模した実体（`fake_gh`）を PATH へ置き、`_fetch_changed_files` の実際の
呼び出し（REST 失敗 → `gh pr view --json files` 成功）を通す。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest

PR = 7200
REPO = "o/r"

# gh pr view --json files が返す代表的な files JSON（fallback 側）。
_FALLBACK_FILES = json.dumps({
    "files": [
        {"path": "src/app.py", "changeType": "MODIFIED"},
        {"path": "docs/readme.md", "changeType": "ADDED"},
    ]
})


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def stub_init_scaffolding(monkeypatch, state_mod, tmp_path):
    """worktree 作成・既存コメント取得・認証確認など、対象外の副作用をスタブする。"""
    worktree = tmp_path / "wt"
    worktree.mkdir()

    monkeypatch.setattr(
        state_mod, "_fetch_pr_metadata",
        lambda pr, repo=None: state_mod.PrMetadata(
            REPO, "takemi", "feat/x", "abc123", "develop", False, 4000, None),
    )
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "takemi")
    monkeypatch.setattr(state_mod, "_create_worktree", lambda *a: None)
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda p: True)
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")

    # `fetch-pr-comments.sh` の呼び出しだけを差し替える。他（`_fetch_changed_files` が
    # 呼ぶ `gh api` / `gh pr view`）は本物の `subprocess.run` を通し、`fake_gh` が模した
    # `gh` で処理する。
    real_run = subprocess.run

    def _run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and "fetch-pr-comments.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(state_mod.subprocess, "run", _run)
    return worktree


def _init_args(worktree: pathlib.Path) -> argparse.Namespace:
    return argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=str(worktree),
        focus=None, extra_instructions_file=None, host="claude")


def test_the_rest_files_api_failure_falls_back_to_gh_pr_view(
        state_mod, fake_gh, tmp_dir, stub_init_scaffolding, capsys) -> None:
    """REST の PR files API が空/失敗のとき、`gh pr view --json files` から作られる。"""
    fake_gh.set_rules([
        {"match": f"repos/{REPO}/pulls/{PR}/files", "stdout": "", "exit": 1},
        {"match": f"pr view {PR} --json files", "stdout": _FALLBACK_FILES},
    ])

    state_mod.cmd_init(_init_args(stub_init_scaffolding))

    calls = fake_gh.joined()
    assert any(f"repos/{REPO}/pulls/{PR}/files" in c for c in calls)
    assert any(f"pr view {PR} --json files" in c for c in calls)

    saved = json.loads(
        (tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))
    assert saved["changed_files"] == [
        {"status": "M", "paths": ["src/app.py"]},
        {"status": "A", "paths": ["docs/readme.md"]},
    ]
    # 自動レビュー観点は fallback から作った changed_files を材料に分類される。
    assert "common" in saved["auto_review_categories"]
    assert "docs_only" not in saved["auto_review_categories"]  # コード変更も含むため

    result = capsys.readouterr()
    assert "✅ state 初期化" in result.err
    assert f"PR={PR}" in result.out


def test_the_empty_rest_response_also_falls_back(
        state_mod, fake_gh, tmp_dir, stub_init_scaffolding) -> None:
    """REST が終了コード 0 でも空配列を返すときも fallback を試す。"""
    fake_gh.set_rules([
        {"match": f"repos/{REPO}/pulls/{PR}/files", "stdout": ""},
        {"match": f"pr view {PR} --json files", "stdout": _FALLBACK_FILES},
    ])

    state_mod.cmd_init(_init_args(stub_init_scaffolding))

    saved = json.loads(
        (tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))
    assert len(saved["changed_files"]) == 2
