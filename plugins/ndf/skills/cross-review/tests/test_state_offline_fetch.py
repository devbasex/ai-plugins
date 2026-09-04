"""取得を控えの内側へ入れる（#291、受け入れ条件 1〜3）。

上限に達すると入口の取得で止まり、レビューを 1 巡も進められなかった。値を 2 つに分け、
変わらない値は一度取って状態ファイルへ持つ。

| 区分 | 値 | 扱い |
| --- | --- | --- |
| 変わらない | 所有者と名前 | git の設定から解決する。通信しない |
| 変わらない | 自分のログイン名・作成者・`base_branch` | 一度取って状態ファイルへ持つ |
| ラウンドごとに変わる | `head_branch` / 変更ファイル / 未解決スレッド | 取れなければ進行を止めない |

**再開の入口では、控えの対象を 1 つも読み直さない。** 残るのはラウンドごとに変わる値の
取得だけで、それらは取得できなくても進む側へ倒してある（`_fetch_unresolved_threads` /
`_sync_worktree`）。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 2912
REPO = "devbasex/ai-plugins"

# 控えの対象。再開の入口でこれらを読み直すと、上限のときに入口で止まる。
CACHED_LOOKUPS = ("repo view", "api user", f"pulls/{PR}")


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed_resumable(tmp_dir: pathlib.Path) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps({
        "current_pr": PR,
        "repo": REPO,
        "tmp_dir": str(tmp_dir),
        # 控えとして持っている値。再開ではこれを読み、GitHub へは問い合わせない。
        "head_branch": "feat/x",
        "base_branch": "develop",
        "pr_author": "takemi",
        "viewer_login": "takemi",
        "is_own_pr": True,
        "event_downgrade": True,
        "worktree_path": "",
        "auto_review_categories": ["code"],
        "auto_review_instructions": "コードの観点",
        "review_instructions": "コードの観点",
        "rounds": [],
        "carried_over": None,
        "final": None,
    }), encoding="utf-8")


def _init_args() -> argparse.Namespace:
    return argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=None,
        focus=None, extra_instructions_file=None)


# ---- 受け入れ条件 2: 所有者と名前は git から解決する ----


def test_the_repository_is_resolved_from_git(state_mod) -> None:
    assert state_mod._repo_from_git() == REPO


def test_the_repository_falls_back_to_gh_only_when_git_cannot_answer(
        state_mod, monkeypatch) -> None:
    monkeypatch.setattr(state_mod, "_git_remote_url", lambda: "")
    assert state_mod._repo_from_git() is None


# ---- 受け入れ条件 1 / 3: 再開の入口は控えを読み直さない ----


def test_the_resume_path_does_not_look_up_the_cached_values(
        state_mod, fake_gh, tmp_dir, capsys) -> None:
    _seed_resumable(tmp_dir)
    # 未解決スレッドの照会だけが残る。0 件の応答（空の出力）を返す。
    fake_gh.set_rules([{"match": "graphql", "stdout": ""}])

    state_mod.cmd_init(_init_args())

    calls = fake_gh.joined()
    assert [c for c in calls if any(k in c for k in CACHED_LOOKUPS)] == []
    assert "RESUMED=1" in capsys.readouterr().out


def test_the_only_remaining_lookup_is_the_round_varying_one(
        state_mod, fake_gh, tmp_dir) -> None:
    """残る取得はラウンドごとに変わる値だけである。数と中身を固定する。"""
    _seed_resumable(tmp_dir)
    fake_gh.set_rules([{"match": "graphql", "stdout": ""}])

    state_mod.cmd_init(_init_args())

    calls = fake_gh.joined()
    assert len(calls) == 1
    assert "graphql" in calls[0] and "reviewThreads" in calls[0]


def test_the_resume_survives_the_rate_limit(state_mod, fake_gh, tmp_dir,
                                            capsys) -> None:
    """上限のもとでも再開できる。これが #291 で止まっていた入口である。"""
    _seed_resumable(tmp_dir)
    fake_gh.set_mode("rate_limit")

    state_mod.cmd_init(_init_args())

    out = capsys.readouterr().out
    assert "RESUMED=1" in out
    assert f"REPO='{REPO}'" in out or f"REPO={REPO}" in out


# ---- 自分のログイン名を控えにする ----


def test_the_viewer_login_is_kept_in_the_state(state_mod, tmp_dir, monkeypatch,
                                               tmp_path) -> None:
    """一度取ったログイン名を状態ファイルへ持つ。待ち行列の冪等の照合が使う。"""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", lambda pr, repo=None:
                        state_mod.PrMetadata(REPO, "takemi", "feat/x", "abc",
                                             "develop", False, 4000, None))
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "takemi")
    monkeypatch.setattr(state_mod, "_create_worktree", lambda *a: None)
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda p: True)
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(state_mod.subprocess, "run", lambda *a, **k:
                        __import__("types").SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    args = _init_args()
    args.worktree = str(worktree)

    state_mod.cmd_init(args)

    saved = json.loads(
        (tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))
    assert saved["viewer_login"] == "takemi"
