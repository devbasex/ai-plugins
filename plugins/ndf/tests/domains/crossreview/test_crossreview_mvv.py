"""cross-review が、承認済みのプロジェクト MVV とベースのコミットを起動の始めに決めて使い続けること（#4076・#4090）。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from ndf.domains.crossreview import criteria_decide, store, threads
from ndf.domains.records import mvv

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import IMPACT, PR, Fake, Recording, _run, _state

# 作業場所に作る MVV の本文のパス（このリポジトリ自身の MVV の文書は読まない）
NDF_DIR = ".ndf"
BODY_AT = f"{NDF_DIR}/mvv.md"
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
    commit(tree, BODY_AT, body, "MVV の本文")
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
    commit(repos.other, BODY_AT, BODY + "3. **PR が足した**\n", "本文を変える")
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
    commit(repos.main, BODY_AT, BODY, "本文")
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
    commit(repos.other, BODY_AT, BODY, "本文を戻す")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    fake = _fixing(tmp_path)
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert "承認の記録と合わない" in got["summary"] and BODY_AT in got["summary"]
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
def test_issue_4090_9_stale_local_base_is_not_used(base: str, repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
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
    # ブランチ名を渡した起動では、マージ先を GitHub へ読みに行かない
    assert hub.port.base_reads == 0


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


def _gathered_in_sprint_branch(repos: Repos) -> str:
    """`main` には承認済みの MVV のバージョン 11、レビュー中のブランチには勝手に上げたバージョン 99 がある状態にして、
    そのブランチの途中のコミットを返す。"""
    _push_base(repos, 11)
    _merge_base_into_pr(repos)
    _approve(repos.other, 99, BODY + "3. **レビュー中のブランチが足した**\n")
    gathered = commit(repos.other, "unit.md", "# 単位\n", "集めた単位")
    commit(repos.other, "after.md", "# 後\n", "集めた後の変更")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    return gathered


@pytest.mark.parametrize("form", ["full", "short", "tag"])
def test_base_given_as_a_commit_reads_the_mvv_from_the_merge_target(
    form: str, repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, mvv_state: Path, hub
) -> None:
    """`--base` がコミット（SHA・短縮した SHA・タグ）なら、MVV は PR のマージ先のブランチから読み、レビュー中のブランチが変えた MVV を使わない。
    差分は渡されたコミットと比べる。"""
    gathered = _gathered_in_sprint_branch(repos)
    sh(wt, "fetch", "-q", "origin")
    sh(wt, "tag", "gathered", gathered)
    fake = _fixing(tmp_path)
    given = {"full": gathered, "short": gathered[:10], "tag": "gathered"}[form]
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", given)
    assert (code, got["status"]) == (0, "ok")
    body, _ = _criterion_paths(_prompt(fake, "codex"), mvv_state)
    assert "バージョン 11" in _prompt(fake, "codex") and body.read_text(encoding="utf-8") == BODY
    assert [r["base"] for r in _state(fake.state_dir)["rounds"]] == [gathered, gathered]
    diff = (fake.state_dir / "round1.diff").read_text(encoding="utf-8")
    assert "after.md" in diff and "unit.md" not in diff and "mvv" not in diff
    assert hub.port.base_reads == 1


def test_unreadable_merge_target_stops_before_any_agent(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """`--base` がコミットの SHA で PR のマージ先を読めなければ、渡されたコミットの MVV で代用せず、担当を起動せずに止める。"""
    gathered = _gathered_in_sprint_branch(repos)
    hub.port.base_ref = None
    fake = _fixing(tmp_path)
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", gathered)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert "マージ先" in got["summary"] and "PR を読めない" in got["summary"]
    assert fake.calls == []
    assert not store.state_pr(fake.state_dir, PR).path.exists()


def test_merge_target_is_the_base_of_the_pull_request() -> None:
    """マージ先はスレッドの一覧と同じ応答から読む。続くスレッドの読み取りは 1 回だけその一覧を使い、次は読み直す。"""

    class Hub:
        base = "release"
        reads = 0

        def repository(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
            self.reads += 1
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
            return {"pullRequest": {"baseRefName": self.base, "reviewThreads": page}}

    hub = Hub()
    port = threads.GitHubPort(hub, None, PR, RuntimeError)
    assert (port.base(), port.open_threads(), port.base(), hub.reads) == ("release", [], "release", 1)
    port.open_threads()
    assert hub.reads == 2
    hub.base = None
    with pytest.raises(RuntimeError, match="マージ先"):
        threads.GitHubPort(hub, None, PR, RuntimeError).base()


@pytest.mark.parametrize("item", ["P1", "C1"])
def test_redline_findings_wait_for_a_person(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, item: str) -> None:
    """必ず人の承認が要る操作の指摘は、軽い重さでも修正担当へ渡さず、未解決の欠けとして止めて人の判断へ戻す。"""
    body = BODY + "\n## 必ず人の承認が要る操作\n\n| # | 操作 | 理由 |\n| --- | --- | --- |\n| P1 | 消す | 戻せない |\n"
    _push_base(repos, body=body)
    held = {"text": "人に聞く操作", "severity": "minor", "mvv": item}
    fake = Recording(tmp_path / "st", {"codex": [[{**MAJOR, "mvv": "Value 1"}, held], []], "kiro": [[], []]})
    _, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "incomplete")
    assert [g["reason"] for g in got["gaps"]] == ["人に聞く操作"]
    assert "止まる" in _prompt(fake, "claude") and "人に聞く操作" not in _prompt(fake, "claude")
    assert any(item in w and "人の判断" in w for w in got["warnings"])
    rows = {r["text"]: r for r in _state(fake.state_dir)["findings"]}
    assert (rows["人に聞く操作"]["mvv"], rows["止まる"]["mvv"], rows["止まる"]["outcome"]) == (item, "Value 1", "fixed")
