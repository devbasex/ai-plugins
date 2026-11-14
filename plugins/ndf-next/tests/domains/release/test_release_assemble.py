"""リリースのブランチの組み立て（本物の git と、偽の GitHub）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.release import assemble

from ._fakes import FakeHub, git, init_repo, write_json


def _node(number: int, title: str, conclusion: str) -> dict:
    run = {"__typename": "CheckRun", "name": "pytest", "status": "COMPLETED", "conclusion": conclusion}
    skipped = {"__typename": "CheckRun", "name": "smoke", "status": "COMPLETED", "conclusion": "SKIPPED"}
    status = {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS"}
    rollup = {"contexts": {"nodes": [run, skipped, status]}}
    return {"number": number, "title": title, "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]}}


def _origin_with_change(tmp_path: Path, test_only: bool) -> Path:
    """本番（main）の後に、ベースブランチ（develop）へ PR #5 をマージした origin と、その clone。"""
    seed = init_repo(tmp_path / "seed")
    write_json(seed / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]})
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (seed / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 前\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "main")
    git(seed, "switch", "-q", "-c", "feat/x")
    changed = seed / "tools" / "kit" / ("tests/test_a.py" if test_only else "a.py")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("x = 1\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "change")
    git(seed, "switch", "-q", "develop")
    git(seed, "merge", "-q", "--no-ff", "feat/x", "-m", "Merge pull request #5 from o/feat/x")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", "-b", "develop", str(origin), str(work))
    git(work, "config", "user.email", "t@example.invalid")
    git(work, "config", "user.name", "t")
    git(work, "config", "commit.gpgsign", "false")
    return work


def _hub() -> FakeHub:
    hub = FakeHub(node_data={5: _node(5, "直した", "SUCCESS")})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 9, "html_url": "https://example.invalid/pull/9"}
    hub.responses[("PATCH", "/repos/o/r/issues/9")] = {"html_url": "https://example.invalid/pull/9"}
    return hub


def test_assemble_builds_branch_and_pull_with_materials(tmp_path: Path) -> None:
    work = _origin_with_change(tmp_path, test_only=False)
    hub = _hub()
    built = assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert (built.branch, built.version, built.pr) == ("release/v1.2.4", "1.2.4", 9)
    shown = git(work, "show", "origin/release/v1.2.4:tools/kit/.claude-plugin/plugin.json")
    assert json.loads(shown)["version"] == "1.2.4"
    log = git(work, "show", "origin/release/v1.2.4:CHANGELOG.md")
    assert log.index("## [kit 1.2.4]") < log.index("## [kit 1.2.3]") and "- 直した（#5）" in log
    create = next(p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/pulls"))
    assert (create["head"], create["base"]) == ("release/v1.2.4", "main")
    body = next(p for m, path, p in hub.requests if m == "PATCH")["body"]
    assert "- #5 直した（CI 2/2（スキップ 1））" in body and f"`{built.facts.head}`" in body
    assert built.facts.head == git(work, "rev-parse", "HEAD").strip()


def test_issue_2217_tests_only_change_is_not_released(tmp_path: Path) -> None:
    """#2217: テストだけの変化は、宣言が無ければ版を上げず、ブランチも PR も作らない。"""
    work = _origin_with_change(tmp_path, test_only=True)
    hub = _hub()
    with pytest.raises(assemble.NothingToRelease):
        assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert hub.requests == []
