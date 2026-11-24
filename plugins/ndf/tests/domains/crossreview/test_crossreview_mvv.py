"""cross-review が、承認済みのプロジェクト MVV とベースのコミットを起動の始めに決めて使い続けること（#4076・#4090）。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from ndf.domains.crossreview import criteria_decide, store
from ndf.domains.records import mvv

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import IMPACT, PR, Fake, Recording, _run, _state

BODY = "# 見本の MVV\n\n## Mission\n\n使命\n\n## Value\n\n1. **一つ目**\n2. **二つ目**\n"
MAJOR = {"text": "止まる", "severity": "major", "impact": IMPACT}


@pytest.fixture(autouse=True)
def mvv_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """MVV のスナップショットの保存先を一時ディレクトリにする。"""
    state = tmp_path / "mvv-state"
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(state))
    return state


def _approve(tree: Path, version: int, body: str = BODY, approved: str | None = None) -> None:
    """`tree` のブランチへ、`version` で承認した MVV の設定と本文をコミットする。`approved` は、承認の記録に残す sha256 の元の本文（省けば `body`）。"""
    sha = hashlib.sha256((approved if approved is not None else body).encode("utf-8")).hexdigest()
    commit(tree, ".ndf/mvv.md", body, "MVV の本文")
    commit(tree, ".ndf/mvv.json", json.dumps({"versions": [{"version": version, "sha256": sha}]}), "MVV の設定")


def _push_base(repos: Repos, version: int = 11, **how: Any) -> None:
    """リモートの `main` だけを進める（手元の `main` とリモート追跡参照は古いまま）。"""
    sh(repos.other, "fetch", "-q", "origin")
    sh(repos.other, "checkout", "-q", "-B", "base", "origin/main")
    _approve(repos.other, version, **how)
    sh(repos.other, "push", "-q", "origin", "HEAD:refs/heads/main")
    sh(repos.other, "checkout", "-q", BRANCH)
    sh(repos.other, "fetch", "-q", "origin")


def _merge_base_into_pr(repos: Repos) -> None:
    """PR のブランチへ `main` をマージして送る。"""
    sh(repos.other, "reset", "-q", "--hard", f"origin/{BRANCH}")
    sh(repos.other, "merge", "-q", "--no-edit", "origin/main")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")


def _fixing(tmp_path: Path) -> Recording:
    return Recording(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})


def _prompt(fake: Recording, agent: str) -> str:
    return next(p for a, _, p in fake.prompts if a == agent)


def _criterion_paths(prompt: str, mvv_state: Path) -> list[Path]:
    """指示の基準 5 の行に書かれたスナップショットのパス。"""
    [line] = [x for x in prompt.splitlines() if x.startswith("5. ")]
    return [Path(p) for p in re.findall(re.escape(str(mvv_state)) + r"/[^）\s]+\.md", line)]


def test_approved_mvv_of_the_base_reaches_both_prompts_as_snapshots(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, mvv_state: Path
) -> None:
    """PR が MVV の本文を変えていても、相手役と修正担当には、ベースブランチの承認済みの本文と NDF の共通原則の
    スナップショットのパスを渡す。"""
    _push_base(repos)
    _merge_base_into_pr(repos)
    commit(repos.other, ".ndf/mvv.md", BODY + "3. **PR が足した**\n", "本文を変える")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    fake = _fixing(tmp_path)
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (code, got["status"]) == (0, "ok")
    for agent in ("codex", "kiro", "claude"):
        body, principles = _criterion_paths(_prompt(fake, agent), mvv_state)
        assert "バージョン 11" in _prompt(fake, agent)
        assert (body.read_text(encoding="utf-8"), principles.read_bytes()) == (BODY, mvv.principles())
    assert '"mvv"' in _prompt(fake, "codex").splitlines()[-1]


def test_unapproved_mvv_adds_nothing_to_the_prompts(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, mvv_state: Path) -> None:
    """MVV がまだ承認されていなければ、指示に MVV の基準も `mvv` のキーも載せず、スナップショットも書かない。"""
    commit(repos.main, ".ndf/mvv.json", json.dumps({"versions": []}), "設定だけ")
    commit(repos.main, ".ndf/mvv.md", BODY, "本文")
    sh(repos.main, "push", "-q", "origin", "HEAD:refs/heads/main")
    fake = _fixing(tmp_path)
    _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert all(line in _prompt(fake, "codex") for line in criteria_decide.reviewer_block(()))
    assert not any(x.startswith("5. ") for _, _, p in fake.prompts for x in p.splitlines())
    assert '"mvv"' not in _prompt(fake, "codex")
    assert not mvv_state.exists()


def test_mismatched_mvv_of_the_base_stops_before_any_agent(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """ベースブランチの MVV の本文が承認された内容と違えば、PR が本文を元に戻していても、担当を起動せずに止める。"""
    _push_base(repos, body=BODY + "勝手に足した\n", approved=BODY)
    _merge_base_into_pr(repos)
    commit(repos.other, ".ndf/mvv.md", BODY, "本文を戻す")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    fake = _fixing(tmp_path)
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert "承認の記録と合わない" in got["summary"] and ".ndf/mvv.md" in got["summary"]
    assert fake.calls == []
    assert not store.state_pr(fake.state_dir, PR).path.exists()


def test_snapshot_that_cannot_be_written_stops_before_any_agent(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, mvv_state: Path
) -> None:
    """スナップショットを書けなければ、MVV を確かめないレビューを始めずに止める。"""
    _push_base(repos)
    mvv_state.write_text("ディレクトリでない", encoding="utf-8")
    fake = _fixing(tmp_path)
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert fake.calls == []


def test_base_missing_on_the_remote_stops_before_any_agent(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """ベースブランチをリモートから取得できなければ、手元に同じ名前のブランチがあっても使わずに止める。"""
    sh(wt, "branch", "stack", "origin/main")
    fake = _fixing(tmp_path)
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", "stack")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert fake.calls == []


@pytest.mark.parametrize("base", ["main", "origin/main"])
def test_issue_4090_9_stale_local_base_is_not_used(
    base: str, repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, mvv_state: Path
) -> None:
    """手元の `main` が古くても、MVV と差分はリモートの最新の `main` から作る。`main` に入っている変更は、相手役へ渡す
    差分にも、詳細レビュー済みのセクションにも現れない。"""
    _push_base(repos, 12)
    remote = sh(repos.origin, "rev-parse", "main").strip()
    _merge_base_into_pr(repos)
    assert sh(wt, "rev-parse", "main").strip() == sh(wt, "rev-parse", "origin/main").strip() != remote
    fake = _fixing(tmp_path)
    _run(capsys, monkeypatch, fake, wt, "--base", base)
    diff = (fake.state_dir / "round1.diff").read_text(encoding="utf-8")
    assert "セクション B" in diff and "mvv" not in diff
    state = _state(fake.state_dir)
    assert [r["base"] for r in state["rounds"]] == [remote, remote]
    assert state["reviewed_sections"] and not any("mvv" in x for x in state["reviewed_sections"])
    assert "バージョン 12" in _prompt(fake, "codex")


def test_issue_4090_9_base_moved_during_the_run_is_not_used(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """起動の途中で別のセッションがベースブランチの参照を進めても、次のラウンドは起動の始めのコミットと比べる。"""
    fixed = sh(wt, "rev-parse", "origin/main").strip()
    fake = Fake(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})

    def move() -> None:
        for ref in ("refs/heads/main", "refs/remotes/origin/main"):
            sh(wt, "update-ref", ref, f"origin/{BRANCH}")

    fake.after_commit = move
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (code, got["status"]) == (0, "ok")
    assert [r["base"] for r in _state(fake.state_dir)["rounds"]] == [fixed, fixed]


def test_base_given_as_a_commit_is_used_without_fetching_a_branch(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """`--base` にコミットの SHA を渡すと、ブランチとして取得せず、そのコミットと比べる。"""
    gathered = sh(wt, "rev-parse", "origin/main").strip()
    fake = _fixing(tmp_path)
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", gathered)
    assert (code, got["status"]) == (0, "ok")
    assert {r["base"] for r in _state(fake.state_dir)["rounds"]} == {gathered}
