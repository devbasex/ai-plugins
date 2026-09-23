"""ラウンドの開始（`start-round`）で控えを取り直し、前のラウンドからの変更の節を書く（#542）。

| 機能 | 何を見るか |
| --- | --- |
| 控えの取り直し（決定 6） | 2 ラウンド目以降だけ取り直す / 失敗したら前の控えを残して `⚠` / 巻き直しの後は新しい PR |
| 変更の節（決定 4） | 同じ PR の前のラウンドと head が違うときだけ書く / 一覧の打ち切り / 前の起動の残りを消す |

GitHub は呼ばない。控えの取得は偽の `fetch-pr-comments.sh` に差し替え、変更の一覧は一時の
git リポジトリで作る。
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


def _commit(repo: pathlib.Path, names: list[str]) -> str:
    for n in names:
        p = repo / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(p.read_text(encoding="utf-8") + "x\n" if p.exists() else "x\n",
                     encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


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


def _changes(tmp_path, round_no: int) -> pathlib.Path:
    return tmp_path / f"cross-review-pr{PR}-round{round_no}-changes.md"


def _existing(tmp_path) -> pathlib.Path:
    return tmp_path / f"cross-review-pr{PR}-existing-comments.txt"


# ---------- 前のラウンドからの変更の節（AC9〜AC11） ----------

def test_the_second_round_gets_the_changed_files(start, repo, tmp_path, fake_fetch):
    """AC9: 同じ PR の前のラウンドと head が違えば、2 つの SHA とファイル名を書く。"""
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, ["issues/x-design.md", "a.md"])
    start.write_state([{"round": 1, "pr": PR, "head_sha": before, "reviewers": ["codex"]}])
    start(after)
    text = _changes(tmp_path, 2).read_text(encoding="utf-8")
    assert before in text and after in text
    assert "- a.md" in text and "- issues/x-design.md" in text
    assert f"git diff {before} {after}" in text


@pytest.mark.parametrize("case", ["first", "same_head", "no_head", "other_pr"])
def test_no_changes_section_without_a_comparable_previous_round(
        start, repo, tmp_path, fake_fetch, case):
    """AC10: 1 ラウンド目・同じ head・head の無いラウンド・PR の切り替え直後は書かず、残りも消す。"""
    head = _git(repo, "rev-parse", "HEAD")
    other = _commit(repo, ["b.md"])
    rounds = {
        "first": [],
        "same_head": [{"round": 1, "pr": PR, "head_sha": other, "reviewers": ["codex"]}],
        "no_head": [{"round": 1, "pr": PR, "reviewers": ["codex"]}],
        "other_pr": [{"round": 1, "pr": PR - 1, "head_sha": head, "reviewers": ["codex"]}],
    }[case]
    start.write_state(rounds)
    round_no = len(rounds) + 1
    _changes(tmp_path, round_no).write_text("古い節", encoding="utf-8")
    start(other)
    assert not _changes(tmp_path, round_no).exists()


def test_the_list_stops_at_fifty_files(start, repo, tmp_path, fake_fetch):
    """AC11: 53 ファイルなら 50 件と「ほか 3 件」。"""
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, [f"f{i:02d}.md" for i in range(53)])
    start.write_state([{"round": 1, "pr": PR, "head_sha": before, "reviewers": ["codex"]}])
    start(after)
    lines = _changes(tmp_path, 2).read_text(encoding="utf-8").splitlines()
    assert sum(1 for l in lines if l.startswith("- f")) == 50
    assert "- ほか 3 件" in lines


def test_the_list_stops_at_five_thousand_bytes(start, repo, tmp_path, fake_fetch):
    """AC11 と非機能: 名前が 200 バイトのファイル 40 件でも、節は 6,000 バイト以下。"""
    before = _git(repo, "rev-parse", "HEAD")
    names = [f"d/{i:02d}" + "n" * 195 for i in range(40)]
    after = _commit(repo, names)
    start.write_state([{"round": 1, "pr": PR, "head_sha": before, "reviewers": ["codex"]}])
    start(after)
    path = _changes(tmp_path, 2)
    listed = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("- d/")]
    assert sum(len(l.encode()) + 1 for l in listed) <= 5000
    assert len(listed) < 40
    assert f"- ほか {40 - len(listed)} 件" in path.read_text(encoding="utf-8")
    assert path.stat().st_size <= 6000


def test_a_failed_diff_is_reported_and_the_round_goes_on(start, repo, tmp_path, fake_fetch, capsys):
    """前の head が作業ツリーに無ければ書かずに `⚠` を出して続ける。"""
    head = _git(repo, "rev-parse", "HEAD")
    start.write_state([{"round": 1, "pr": PR, "head_sha": "f" * 40, "reviewers": ["codex"]}])
    start(head)
    assert not _changes(tmp_path, 2).exists()
    assert "⚠ 前のラウンドからの変更を取れませんでした" in capsys.readouterr().err


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
