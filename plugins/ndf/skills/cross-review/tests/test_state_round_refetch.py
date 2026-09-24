"""ラウンドの開始（`start-round`）で既存コメントの控えを取り直す（#542 の決定 6）。

2 ラウンド目以降だけ取り直す / 失敗したら前の控えを残して `⚠` / 巻き直しの後は新しい PR。
GitHub は呼ばない。控えの取得は偽の `fetch-pr-comments.sh` に差し替える。
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

PR = 7100
REPO = "acme/demo"

# conftest の見張り（`gh` の実行を落とす）より前の実物。git と偽のスクリプトの起動に使う。
_RUN = subprocess.run


def _git(repo: pathlib.Path, *argv: str) -> str:
    return _RUN(["git", "-C", str(repo), *argv], check=True,
                capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path) -> pathlib.Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@example.com")
    _git(wt, "config", "user.name", "t")
    (wt / "a.md").write_text("a\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "first")
    return wt


@pytest.fixture()
def fake_fetch(tmp_path, state_mod, monkeypatch):
    """偽の `fetch-pr-comments.sh`。呼び出しの引数を記録し、`FAKE_RC` の終了コードで終わる。"""
    log = tmp_path / "fetch.log"
    script = tmp_path / "fetch-pr-comments.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'echo "[PR-COMMENT] [bot] ${FAKE_BODY:-new}"\n'
        'exit "${FAKE_RC:-0}"\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(state_mod, "FETCH_COMMENTS_SCRIPT", script)
    monkeypatch.setattr(state_mod.subprocess, "run", _RUN)

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls


@pytest.fixture()
def start(state_mod, monkeypatch, tmp_path, repo):
    """状態ファイルを置き、`start-round` を呼ぶ。同期は head を返すだけにする。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(state_mod, "_auto_flush", lambda pr: None)
    monkeypatch.setattr(state_mod, "_guard_previous_round", lambda st, prev: None)
    heads: dict[str, str] = {}
    monkeypatch.setattr(
        state_mod, "_sync_before_round",
        lambda st, pr: state_mod.HeadRef("feat/x", heads["oid"], False) if heads.get("oid") else None)

    def write_state(rounds: list[dict], current_pr: int = PR) -> None:
        st = {
            "started_at": "x", "max_rounds": 12, "rotate_after": 8, "only": "codex",
            "current_pr": current_pr, "worktree_path": str(repo), "tmp_dir": str(tmp_path),
            "repo": REPO, "head_branch": "feat/x", "host": "claude",
            "pr_history": [], "rounds": rounds, "final": None,
        }
        (tmp_path / f"cross-review-pr{PR}-state.json").write_text(
            json.dumps(st), encoding="utf-8")

    def run(head: str | None) -> None:
        heads["oid"] = head
        state_mod.cmd_start_round(type("A", (), {"pr": PR})())

    run.write_state = write_state
    return run


def _existing(tmp_path) -> pathlib.Path:
    return tmp_path / f"cross-review-pr{PR}-existing-comments.txt"


# ---------- 既存コメントの控えの取り直し（AC12〜AC13） ----------

def test_the_second_round_refetches_the_comments(start, repo, tmp_path, fake_fetch, monkeypatch):
    """AC12: 2 ラウンド目で取り直し、控えが新しい中身になる。`--strict` を付ける。"""
    head = _git(repo, "rev-parse", "HEAD")
    _existing(tmp_path).write_text("[PR-COMMENT] [bot] old\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_BODY", "round1-finding")
    start.write_state([{"round": 1, "pr": PR, "head_sha": head, "reviewers": ["codex"]}])
    start(head)
    assert fake_fetch() == [f"--strict {REPO} {PR}"]
    assert "round1-finding" in _existing(tmp_path).read_text(encoding="utf-8")


def test_the_first_round_does_not_refetch(start, repo, tmp_path, fake_fetch):
    """AC12: 通しの 1 ラウンド目は `init` が取った直後のため取り直さない。"""
    start.write_state([])
    start(_git(repo, "rev-parse", "HEAD"))
    assert fake_fetch() == []


def test_after_rotation_the_new_pr_is_fetched(start, repo, tmp_path, fake_fetch):
    """AC12b: 巻き直しの後の最初のラウンドは新しい PR の番号で取り直す。"""
    head = _git(repo, "rev-parse", "HEAD")
    start.write_state([{"round": 1, "pr": PR, "head_sha": head, "reviewers": ["codex"]}],
                      current_pr=PR + 1)
    start(head)
    assert fake_fetch() == [f"--strict {REPO} {PR + 1}"]


def test_a_failed_refetch_keeps_the_previous_snapshot(
        start, repo, tmp_path, fake_fetch, monkeypatch, capsys):
    """AC12a AC13: 取得元の 1 つでも失敗すれば（終了コード 1）前の控えを残し、`⚠` で続ける。"""
    head = _git(repo, "rev-parse", "HEAD")
    _existing(tmp_path).write_text("[PR-COMMENT] [bot] old\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_RC", "1")
    start.write_state([{"round": 1, "pr": PR, "head_sha": head, "reviewers": ["codex"]}])
    start(head)
    assert _existing(tmp_path).read_text(encoding="utf-8") == "[PR-COMMENT] [bot] old\n"
    assert "⚠ 既存コメントの控えを取り直せませんでした" in capsys.readouterr().err
    st = json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())
    assert len(st["rounds"]) == 2


def test_init_fetch_is_not_strict(state_mod, tmp_path, fake_fetch, monkeypatch):
    """AC12a: `init` の取得は `--strict` を付けず、失敗したら理由を返す。"""
    path = tmp_path / "existing.txt"
    assert state_mod._fetch_existing_comments(REPO, PR, path, strict=False) is None
    assert fake_fetch() == [f"{REPO} {PR}"]
    assert path.read_text(encoding="utf-8").startswith("[PR-COMMENT]")
    monkeypatch.setenv("FAKE_RC", "1")
    assert state_mod._fetch_existing_comments(REPO, PR, path, strict=False)


def test_init_aborts_without_a_state_file_when_the_fetch_fails(
        state_mod, tmp_path, fake_fetch, monkeypatch):
    """`init` は控えの取得に失敗すると中断し、状態ファイルを作らない（R2-005 の現状固定）。

    重複検出が無効のままレビューを始めないためである。取得は `--strict` を付けない 1 回。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
    monkeypatch.setenv("FAKE_RC", "1")
    worktree = tmp_path / "wt-new"
    monkeypatch.setattr(
        state_mod, "_fetch_pr_metadata",
        lambda pr, repo=None: state_mod.PrMetadata(
            REPO, "someone", "feat/x", "abc123", "develop", True, 4000, None))
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(state_mod, "_create_worktree",
                        lambda wt, pr, head: pathlib.Path(wt).mkdir())
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "me")
    args = type("A", (), {
        "pr": PR, "max_rounds": 12, "rotate_after": 8, "only": None,
        "worktree": str(worktree), "focus": None, "extra_instructions_file": None,
        "host": "claude"})()

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_init(args)

    assert e.value.code != 0
    assert not (tmp_path / f"cross-review-pr{PR}-state.json").exists()
    assert fake_fetch() == [f"{REPO} {PR}"]


def test_a_refetch_that_cannot_start_keeps_going(
        start, repo, tmp_path, fake_fetch, state_mod, monkeypatch, capsys):
    """取得の起動が OSError を送出しても `⚠` で続け、round を 1 つだけ開く（PR #930 の指摘）。"""
    head = _git(repo, "rev-parse", "HEAD")
    _existing(tmp_path).write_text("[PR-COMMENT] [bot] old\n", encoding="utf-8")
    monkeypatch.setattr(state_mod, "FETCH_COMMENTS_SCRIPT", tmp_path / "missing.sh")
    start.write_state([{"round": 1, "pr": PR, "head_sha": head, "reviewers": ["codex"]}])
    start(head)
    assert _existing(tmp_path).read_text(encoding="utf-8") == "[PR-COMMENT] [bot] old\n"
    assert "⚠ 既存コメントの控えを取り直せませんでした" in capsys.readouterr().err
    st = json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())
    assert len(st["rounds"]) == 2


def test_an_interrupted_refetch_does_not_open_a_round(
        start, repo, tmp_path, state_mod, monkeypatch):
    """取得の途中で割り込まれても、結果の無い round を状態ファイルへ残さない。"""
    head = _git(repo, "rev-parse", "HEAD")

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(state_mod, "_fetch_existing_comments", interrupted)
    start.write_state([{"round": 1, "pr": PR, "head_sha": head, "reviewers": ["codex"]}])
    with pytest.raises(KeyboardInterrupt):
        start(head)
    st = json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())
    assert len(st["rounds"]) == 1
