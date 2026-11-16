"""`release-steps.py land` の GitHub 側（#3672 の I5・I7）の単体テスト。

手元の git で読む部分（版数・先端・取り込みの記録）は、bare の origin を持つ一時のリポジトリで確かめる。
GitHub の API は呼ばない（`approval_of`・`GhPort.pull` を差し替える）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]
import branch_strategy as bs  # noqa: E402
from release_lib import approval_trail, land  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "branch_strategy"


def strategy(kind: str, **release) -> bs.Strategy:
    sec = json.loads((FIX / f"{kind}.json").read_text(encoding="utf-8"))
    sec["release"].update(release)
    return bs.Strategy(sec)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def write_version(root: Path, version: str) -> None:
    p = root / "plugins" / "demo" / ".claude-plugin" / "plugin.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": "demo", "version": version}), encoding="utf-8")


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def repo_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    write_version(root, "1.0.0")
    commit(root, "init")
    git(root, "push", "-q", "origin", "main")
    return root


def test_store_version_reads_each_store():
    files = {
        "plugins/demo/.claude-plugin/plugin.json": json.dumps({"version": "2.1.0"}),
        "package.json": json.dumps({"version": "3.0.0"}),
    }
    show = files.get
    assert land.store_version(strategy("plugin", plugin="demo"), show) == "2.1.0"
    assert land.store_version(strategy("web-develop"), show) == "3.0.0"
    assert land.store_version(strategy("docs"), show) is None
    assert land.store_version(strategy("plugin", plugin="other"), show) is None


def test_bump_facts_compare_cut_and_head(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "checkout", "-q", "-b", "bump")
    write_version(root, "1.1.0")
    head = commit(root, "bump")
    git(root, "push", "-q", "origin", f"{head}:refs/pull/7/head")
    port = land.GhPort(root, "o/r", strategy("plugin", plugin="demo"))
    tip = git(root, "rev-parse", "main")
    assert port._bump_facts(7, head, "main") == {"bump": True, "cut_version": "1.0.0", "contains_tip": True, "tip": tip}
    assert port.production("main")["version"] == "1.0.0"

    # 本番ブランチが進むと、head は先端を含まない（strict の確かめが落ちる）
    git(root, "checkout", "-q", "main")
    (root / "x.txt").write_text("x", encoding="utf-8")
    git(root, "push", "-q", "origin", f"{commit(root, 'other')}:main")
    assert port._bump_facts(7, head, "main")["contains_tip"] is False


def test_bump_facts_unknown_when_version_unreadable(tmp_path):
    """版数のファイルを初めて足す PR は、版数を比べられない（None）。承認の免除から外す。"""
    root = repo_with_origin(tmp_path)
    port = land.GhPort(root, "o/r", strategy("plugin", plugin="demo"))
    git(root, "checkout", "-q", "-b", "first")
    git(root, "rm", "-q", "-r", "plugins")
    commit(root, "drop")
    git(root, "push", "-q", "origin", "HEAD:main")
    write_version(root, "1.0.0")
    head = commit(root, "add version")
    git(root, "push", "-q", "origin", f"{head}:refs/pull/8/head")
    got = port._bump_facts(8, head, "main")
    assert got["bump"] is None
    trunk = strategy("plugin", plugin="demo").section
    trunk["release_strategy"], trunk["approval"]["target"] = "trunk", "release_pr"
    assert not land.exempt_from_gate(bs.Strategy(trunk), got)
    assert land.exempt_from_gate(bs.Strategy(trunk), {**got, "bump": False})


def test_checks_failing_skips_trail_status_and_passes_repo_without_ci(monkeypatch):
    """承認の証跡の status は検査に数えない。検査も有効な workflow も無いリポジトリは検査なしで通す。"""
    import gh_checks

    port = land.GhPort(Path("."), "o/r", strategy("docs"))
    monkeypatch.setattr(gh_checks, "fetch_check_runs", lambda repo, sha, empty_ok=False: [])
    replies = {
        "repos/o/r/commits/h/status": {"statuses": [{"context": approval_trail.STATUS_CONTEXT, "state": "failure"}]},
        "repos/o/r/actions/workflows": {"workflows": []},
    }
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: replies.get(path))
    assert port.checks_failing("h") is None
    replies["repos/o/r/actions/workflows"] = {"workflows": [{"state": "active"}]}
    assert "まだ 1 件も無い" in port.checks_failing("h")
    # 本番への push・schedule でだけ動く workflow は PR の head に検査を作らないため、検査なしで通す
    files = {"cd.yml": "on:\n  push:\n    branches: [main]\n  schedule:\n    - cron: '0 0 * * *'\n"}
    monkeypatch.setattr(land.GhPort, "_show", lambda self, rev: files.get)
    replies["repos/o/r/actions/workflows"] = {"workflows": [{"state": "active", "path": "cd.yml"}]}
    assert port.checks_failing("h") is None
    files["cd.yml"] = "on: [pull_request]\n"
    assert "まだ 1 件も無い" in port.checks_failing("h")
    replies["repos/o/r/commits/h/status"] = {"statuses": [{"context": "ci", "state": "pending"}]}
    assert port.checks_failing("h") == "ci=pending"


def test_landed_record_needs_same_tree_and_tip(tmp_path):
    root = repo_with_origin(tmp_path)
    tip = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "a.txt").write_text("a", encoding="utf-8")
    head = commit(root, "feature")
    # squash で入れた形: 親は tip、木は head と同じ、trailer に 3 つの値
    git(root, "checkout", "-q", "main")
    git(root, "checkout", "-q", head, "--", ".")
    commit(root, f"Land #7\n\n{land.trailers(7, head, tip)}")
    git(root, "push", "-q", "origin", "main")
    port = land.GhPort(root, "o/r", strategy("plugin", plugin="demo"))
    rec = port.landed_record("main", 7, None)
    assert rec["head"] == head and rec["tree_ok"] and rec["first_parent_is_tip"]
    assert port.landed_record("main", 8, None) is None


def test_parse_landed_picks_newest_row_of_the_pr():
    rows = [
        "c2\x00t2\x00p2\x00NDF-Landed-PR: 7\nNDF-Approved-Head: h2\nNDF-Tip: p2\n",
        "c1\x00t1\x00p1\x00NDF-Landed-PR: 7\nNDF-Approved-Head: h1\nNDF-Tip: p1\n",
    ]
    got = land.parse_landed("\x1e".join(rows), 7)
    assert got["commit"] == "c2" and got["head"] == "h2" and got["first_parent"] == "p2"
    assert land.parse_landed("\x1e".join(rows), 9) is None


def test_approved_trail_is_the_one_judge_reads():
    t = approval_trail.Trail
    trails = [
        t("a" * 40, "owner", "2026-11-16T01:00:00Z", False, 1),
        t("b" * 40, "guest", "2026-11-16T02:00:00Z", False, 2),
        t("c" * 40, "owner", "2026-11-16T03:00:00Z", True, 3),
    ]
    roles = {"owner": "admin", "guest": "read"}
    got = approval_trail.approved_trail(trails, roles)
    assert got.sha == "a" * 40
    assert approval_trail.judge(trails, roles, got.sha).state == "success"
    assert approval_trail.approved_trail(trails, {}) is None


def test_run_stops_without_trail_and_lands_human_merge(monkeypatch):
    plugin = strategy("plugin", plugin="demo")
    monkeypatch.setattr(land, "approval_of", lambda repo, s, pr: None)
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": False, "head": "h" * 40, "bump": True})
    got = land.land_pr(Path("."), "o/r", plugin, 5)
    assert got["result"] == "stopped" and "承認の証跡が無い" in got["reason"]

    # 承認の対象が版上げの PR なら、版上げでないスプリントの PR は証跡なしで今の head を入れに進む（決定 8）
    seen = []
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": False, "head": "s" * 40, "bump": False})
    monkeypatch.setattr(
        land, "land_approved", lambda port, s, n, a, **kw: seen.append((a.head, kw["verify_checks"])) or {"result": "queued"}
    )
    assert land.land_pr(Path("."), "o/r", plugin, 5)["result"] == "queued" and seen == [("s" * 40, True)]
    # trunk でない取り込み方の本番宛ての PR は、版上げでなくても承認ゲートを通す（承認の免除は trunk だけ）
    got = land.land_pr(Path("."), "o/r", strategy("web-develop"), 5)
    assert got["result"] == "stopped" and "承認の証跡が無い" in got["reason"] and len(seen) == 1
    monkeypatch.undo()
    monkeypatch.setattr(land, "approval_of", lambda repo, s, pr: None)

    recorded = []
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": True, "head": "h" * 40})
    monkeypatch.setattr(land.GhPort, "has_human_record", lambda self, n: False)
    monkeypatch.setattr(land.GhPort, "comment", lambda self, n, body: recorded.append(body) or True)
    assert land.land_pr(Path("."), "o/r", plugin, 5)["result"] == "human"
    assert land.HUMAN_MARK in recorded[0]
