"""`release-steps.py land` の GitHub 側（#3672 の I5・I7）の単体テスト。

手元の git で読む部分（版数・先端・取り込みの記録）は、bare の origin を持つ一時のリポジトリで確かめる。
GitHub の API は呼ばない（`approval_of`・`GhPort.pull` を差し替える）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]
import branch_strategy as bs  # noqa: E402
import release_case  # noqa: E402
from release_lib import approval_trail, land, land_checks  # noqa: E402
from release_lib.workflow_trigger import PullEvent, runs_on_pull_head  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "branch_strategy"


@pytest.fixture(autouse=True)
def no_declared_checks(monkeypatch):
    """手元のリポジトリの CI テストの宣言を読まない（宣言の検査名は個別のテストで差し替える）。"""
    monkeypatch.setattr(land_checks, "declared_checks", lambda root: [])


ST = "repos/o/r/commits/h/status?per_page=100&page=1"
TIP_QUIET = {
    "repos/o/r/commits/t/check-suites?per_page=100&page=1": {"check_suites": []},
    "repos/o/r/commits/t/status?per_page=100&page=1": {"statuses": []},
}


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
        ST: {"statuses": [{"context": approval_trail.STATUS_CONTEXT, "state": "failure"}]},
        "repos/o/r/actions/workflows?per_page=100&page=1": {"workflows": []},
        **TIP_QUIET,
    }
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: replies.get(path))
    assert port.checks_failing("h", {"tip": "t"}) is None
    replies["repos/o/r/actions/workflows?per_page=100&page=1"] = {"workflows": [{"state": "active"}]}
    assert "まだ 1 件も無い" in port.checks_failing("h")
    # 本番への push・schedule でだけ動く workflow は PR の head に検査を作らないため、検査なしで通す
    files = {"cd.yml": "on:\n  push:\n    branches: [main]\n  schedule:\n    - cron: '0 0 * * *'\n"}
    monkeypatch.setattr(land.GhPort, "_show", lambda self, rev: files.get)
    replies["repos/o/r/actions/workflows?per_page=100&page=1"] = {"workflows": [{"state": "active", "path": "cd.yml"}]}
    pull = {"head_ref": "release/v1", "base": "main", "tip": "t"}
    assert port.checks_failing("h", pull) is None
    # head のブランチ名を読めなければ、ブランチで絞った push も動くとみなす
    assert "まだ 1 件も無い" in port.checks_failing("h")
    files["cd.yml"] = "on: [pull_request]\n"
    assert "まだ 1 件も無い" in port.checks_failing("h", pull)
    replies[ST] = {"statuses": [{"context": "ci", "state": "pending"}]}
    assert port.checks_failing("h") == "ci=pending"


def test_no_checks_reason_reads_pull_request_target_from_the_base_tip(monkeypatch):
    """#3672 の round 11: pull_request_target は宛先の先端の定義で起動を決め、PR の head での書き換えで「検査なし」にさせない。"""
    import gh_checks

    port = land.GhPort(Path("."), "o/r", strategy("docs"))
    monkeypatch.setattr(gh_checks, "fetch_check_runs", lambda repo, sha, empty_ok=False: [])
    replies = {
        ST: {"statuses": []},
        "repos/o/r/actions/workflows?per_page=100&page=1": {"workflows": [{"state": "active", "path": "ci.yml"}]},
        **TIP_QUIET,
    }
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: replies.get(path))
    target, cron = "on: [pull_request_target]\n", "on:\n  schedule:\n    - cron: '0 0 * * *'\n"
    trees = {"h": {"ci.yml": cron}, "t": {"ci.yml": target}}
    monkeypatch.setattr(land.GhPort, "_show", lambda self, rev: trees[rev].get)
    pull = {"head_ref": "s", "base": "main", "tip": "t"}
    assert "ci.yml" in port.checks_failing("h", pull)
    # head で足しただけの pull_request_target は宛先の定義に無いため動かない
    trees.update({"h": {"ci.yml": target}, "t": {"ci.yml": cron}})
    assert port.checks_failing("h", pull) is None


def test_no_checks_reason_reads_every_page_of_workflows(monkeypatch):
    """#3672 の round 7: workflow の一覧を全ページ読み、2 ページ目にある PR の CI を見落とさない。"""
    import gh_checks

    port = land.GhPort(Path("."), "o/r", strategy("docs"))
    monkeypatch.setattr(gh_checks, "fetch_check_runs", lambda repo, sha, empty_ok=False: [])
    cron = [{"state": "active", "path": f"cron{i}.yml"} for i in range(100)]
    replies = {
        ST: {"statuses": []},
        "repos/o/r/actions/workflows?per_page=100&page=1": {"total_count": 101, "workflows": cron},
        "repos/o/r/actions/workflows?per_page=100&page=2": {"total_count": 101, "workflows": [{"state": "active", "path": "ci.yml"}]},
    }
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: replies.get(path))
    files = {"ci.yml": "on: [pull_request]\n", **{w["path"]: "on:\n  schedule:\n    - cron: '0 0 * * *'\n" for w in cron}}
    monkeypatch.setattr(land.GhPort, "_show", lambda self, rev: files.get)
    assert "ci.yml" in port.checks_failing("h", {"head_ref": "s", "base": "main"})
    del replies["repos/o/r/actions/workflows?per_page=100&page=2"]
    assert "読めない" in port.checks_failing("h", {"head_ref": "s", "base": "main"})


def quiet_port(monkeypatch, replies, runs=()):
    import gh_checks

    monkeypatch.setattr(gh_checks, "fetch_check_runs", lambda repo, sha, empty_ok=False: list(runs))
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: replies.get(path))
    monkeypatch.setattr(land.GhPort, "_show", lambda self, rev: {}.get)
    return land.GhPort(Path("."), "o/r", strategy("docs"))


def test_checks_failing_reads_every_page_of_statuses(monkeypatch):
    """#3672 の round 8: commit status を全ページ読み、31 件目以降の pending を見落とさない。"""
    ok = [{"context": f"c{i}", "state": "success"} for i in range(100)]
    replies = {
        ST: {"total_count": 101, "statuses": ok},
        "repos/o/r/commits/h/status?per_page=100&page=2": {"total_count": 101, "statuses": [{"context": "late", "state": "pending"}]},
    }
    port = quiet_port(monkeypatch, replies)
    assert port.checks_failing("h") == "late=pending"
    del replies["repos/o/r/commits/h/status?per_page=100&page=2"]
    assert "読めない" in port.checks_failing("h")


def test_checks_failing_waits_for_declared_ci_checks(monkeypatch):
    """#3672 の round 8: CI テストの宣言の検査がまだ登録されていなければ、ほかの検査が成功でも未完了で止める。"""
    port = quiet_port(monkeypatch, {ST: {"statuses": [{"context": "lint", "state": "success"}]}})
    monkeypatch.setattr(land_checks, "declared_checks", lambda root: ["test"])
    assert "test" in port.checks_failing("h") and "始まって" in port.checks_failing("h")
    monkeypatch.setattr(land_checks, "declared_checks", lambda root: ["lint"])
    assert port.checks_failing("h") is None


def test_no_checks_reason_waits_for_external_ci(monkeypatch):
    """#3672 の round 8: workflow が無くても、本番ブランチの先端に Actions 以外の CI の結果があれば、検査の 0 件を通さない。"""
    replies = {ST: {"statuses": []}, "repos/o/r/actions/workflows?per_page=100&page=1": {"workflows": []}, **TIP_QUIET}
    port = quiet_port(monkeypatch, replies)
    assert port.checks_failing("h", {"tip": "t"}) is None
    replies["repos/o/r/commits/t/check-suites?per_page=100&page=1"] = {
        "check_suites": [
            {"app": {"slug": "github-actions"}, "latest_check_runs_count": 3},
            {"app": {"slug": "circleci-checks"}, "latest_check_runs_count": 1},
        ]
    }
    assert "circleci-checks" in port.checks_failing("h", {"tip": "t"})
    replies.update(TIP_QUIET)
    replies["repos/o/r/commits/t/status?per_page=100&page=1"] = {"statuses": [{"context": "ci/jenkins", "state": "success"}]}
    assert "ci/jenkins" in port.checks_failing("h", {"tip": "t"})
    del replies["repos/o/r/commits/t/check-suites?per_page=100&page=1"]
    assert "読めない" in port.checks_failing("h", {"tip": "t"})


def test_queue_reads_every_page_of_entries(monkeypatch):
    """#3672 の round 8: merge queue の entries を pageInfo と after で最後まで読む。"""
    import gh_graphql
    import gh_quota

    pages = {
        None: {"nodes": [{"pullRequest": {"number": 1, "headRefOid": "A"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}},
        "c1": {"nodes": [{"pullRequest": {"number": 2, "headRefOid": "B"}}], "pageInfo": {"hasNextPage": False, "endCursor": "c2"}},
    }
    seen = []

    def graphql(query, variables):
        seen.append(variables["after"])
        return gh_quota.Attempt({"repository": {"mergeQueue": {"entries": pages[variables["after"]]}}})

    monkeypatch.setattr(gh_graphql, "graphql", graphql)
    port = land.GhPort(Path("."), "o/r", strategy("docs"))
    assert [n["pullRequest"]["number"] for n in port._queue_nodes("main")] == [1, 2] and seen == [None, "c1"]
    pages["c1"]["pageInfo"] = {"hasNextPage": True, "endCursor": "c1"}
    with pytest.raises(land.LandError):
        port._queue_nodes("main")


def test_push_files_compare_the_last_push(tmp_path, monkeypatch):
    """#3672 の round 7: push の paths は直近の push の before..after と照らす。途中で変えて最後の push で戻した
    ファイルは PR の変更ファイルに無くても、その push の差分に入る。比較元を決められなければ None（動くとみなす）。"""
    root = repo_with_origin(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x\n", encoding="utf-8")
    first = commit(root, "change src")
    (root / "src" / "a.py").unlink()
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("x\n", encoding="utf-8")
    head = commit(root, "revert src, add docs")
    port = land.GhPort(root, "o/r", strategy("docs"))
    activity = [{"before": first, "after": head, "activity_type": "push"}]
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: activity if "/activity?" in path else None)
    pull_files = lambda: ["docs/a.md"]  # noqa: E731
    assert sorted(land_checks.push_files(port, "s", head, pull_files)) == ["docs/a.md", "src/a.py"]
    on = "on:\n  push:\n    paths: ['src/**']\n"
    assert runs_on_pull_head(on, PullEvent("s", "main", pull_files, lambda: land_checks.push_files(port, "s", head, pull_files)))
    # 新しいブランチの push は PR の変更ファイルで代える
    activity[0]["before"] = "0" * 40
    assert land_checks.push_files(port, "s", head, pull_files) == ["docs/a.md"]
    # 直近の push が head を作っていない・head のブランチ名が無いときは決めない
    activity[0]["after"] = first
    assert land_checks.push_files(port, "s", head, pull_files) is None
    assert land_checks.push_files(port, None, head, pull_files) is None


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


def test_admin_land_counts_only_user_trails():
    """管理者の迂回でマージする宣言では、本番ブランチが人の承認を求める（承認数 1 以上か読めない）とき、
    MVV 判定の証跡を承認に数えない（cd-admin。#3506 の I9）。承認数が 0 なら担い手で絞らない。"""
    t = approval_trail.Trail
    trails = [t("a" * 40, "owner", "2026-11-16T01:00:00Z", False, 1, "user"), t("b" * 40, "owner", "2026-11-16T02:00:00Z", False, 2, "mvv")]
    roles = {"owner": "admin"}
    assert approval_trail.approved_trail(trails, roles).sha == "b" * 40
    assert approval_trail.approved_trail(trails, roles, "user").sha == "a" * 40
    assert approval_trail.approved_trail(trails[1:], roles, "user") is None

    s = strategy("plugin", plugin="demo")
    s.section["merge"].update(admin=True, method="direct")
    for facts, want in ((SimpleNamespace(approvals=1), "user"), (None, "user"), (SimpleNamespace(approvals=0), None)):
        assert land.trail_by(s, facts) == want
    s.section["merge"]["admin"] = False
    assert land.trail_by(s, SimpleNamespace(approvals=1)) is None


def test_run_stops_without_trail_and_lands_human_merge(monkeypatch):
    plugin = strategy("plugin", plugin="demo")
    monkeypatch.setattr(land, "approval_of", lambda repo, s, pr, by=None: None)
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": False, "head": "h" * 40, "bump": True})
    got = land.land_pr(Path("."), "o/r", plugin, 5)
    assert got["result"] == "stopped" and "承認の証跡が無い" in got["reason"]

    # 承認の対象が版上げの PR なら、版上げでないスプリントの PR は証跡なしで今の head を入れに進む（決定 8）
    seen = []
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": False, "head": "s" * 40, "bump": False})
    monkeypatch.setattr(
        land, "land_approved", lambda port, s, n, a, **kw: seen.append((a.head, kw["pull"]["head"])) or {"result": "queued"}
    )
    assert land.land_pr(Path("."), "o/r", plugin, 5)["result"] == "queued" and seen == [("s" * 40, "s" * 40)]
    # trunk でない取り込み方の本番宛ての PR は、版上げでなくても承認ゲートを通す（承認の免除は trunk だけ）
    got = land.land_pr(Path("."), "o/r", strategy("web-develop"), 5)
    assert got["result"] == "stopped" and "承認の証跡が無い" in got["reason"] and len(seen) == 1
    monkeypatch.undo()
    monkeypatch.setattr(land, "approval_of", lambda repo, s, pr, by=None: None)

    recorded = []
    monkeypatch.setattr(land.GhPort, "pull", lambda self, n: {"merged": True, "head": "h" * 40})
    monkeypatch.setattr(land.GhPort, "has_human_record", lambda self, n: False)
    monkeypatch.setattr(land.GhPort, "comment", lambda self, n, body: recorded.append(body) or True)
    assert land.land_pr(Path("."), "o/r", plugin, 5)["result"] == "human"
    assert land.HUMAN_MARK in recorded[0]


def test_changed_files_reads_non_ascii_paths_as_is(tmp_path):
    """日本語のファイル名を引用符と 8 進の形にせず読み、paths の絞り込みに当てる（core.quotePath の既定を使わない）。"""
    root = repo_with_origin(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "設計.md").write_text("x\n", encoding="utf-8")
    sha = commit(root, "docs")
    port = land.GhPort(root, "o/r", strategy("docs"))
    assert land_checks.pull_changed_files(port, "main", sha) == ["docs/設計.md"]


DOCS_ONLY = ["docs/a.md"]


@pytest.mark.parametrize(
    "on, files, runs",
    [
        # push のブランチの絞り込みは head のブランチ名と照らす（#3672 の round 5）
        ("push:\n    branches: [main]", DOCS_ONLY, False),
        ("push:\n    branches-ignore: [main]", DOCS_ONLY, True),
        ("push:\n    branches: ['**']", DOCS_ONLY, True),
        ("push:\n    branches: ['release/*']", DOCS_ONLY, True),
        ("push:\n    branches: ['release/*', '!release/v1']", DOCS_ONLY, False),
        ("push:\n    branches-ignore: ['release/**']", DOCS_ONLY, False),
        ("push:\n    tags: ['v*']", DOCS_ONLY, False),
        # pull_request の branches は宛先、paths・paths-ignore は変更ファイルと照らす
        ("pull_request:\n    paths: ['src/**']", DOCS_ONLY, False),
        ("pull_request:\n    paths: ['src/**']", ["src/x/a.py", "docs/a.md"], True),
        ("pull_request:\n    paths: ['*.md']", DOCS_ONLY, False),
        ("pull_request:\n    paths-ignore: ['docs/**']", DOCS_ONLY, False),
        ("pull_request:\n    paths-ignore: ['docs/**']", ["docs/a.md", "a.py"], True),
        ("pull_request:\n    branches: [develop]", DOCS_ONLY, False),
        ("pull_request:\n    branches: [main]\n    paths: ['docs/**']", DOCS_ONLY, True),
        ("pull_request_target:\n    branches-ignore: [main]", DOCS_ONLY, False),
        # 変更ファイルを読めなければ paths の絞り込みは動くとみなす
        ("pull_request:\n    paths: ['src/**']", None, True),
        # push の paths・paths-ignore も PR の変更ファイルと照らす（どの push の差分も PR の変更ファイルの部分集合）
        ("push:\n    paths: ['src/**']", DOCS_ONLY, False),
        ("push:\n    branches: ['**']\n    paths: ['src/**']", DOCS_ONLY, False),
        ("push:\n    branches: ['**']\n    paths: ['src/**']", ["src/a.py"], True),
        ("push:\n    paths-ignore: ['docs/**']", DOCS_ONLY, False),
        ("push:\n    branches: [main]\n    paths: ['docs/**']", DOCS_ONLY, False),
        # GitHub の書き方: `**/` はディレクトリ 0 個にも当たり、`?` は直前の 1 文字の 0〜1 回
        ("pull_request:\n    paths: ['docs/**/*.md']", DOCS_ONLY, True),
        ("pull_request:\n    paths: ['**/a.md']", DOCS_ONLY, True),
        ("pull_request:\n    paths: ['docs/*.mdx?']", DOCS_ONLY, True),
        ("pull_request:\n    paths: ['docs/?.md']", DOCS_ONLY, False),
        # types は opened・synchronize・reopened のどれかを含むときだけ PR の head で動く（書かなければ既定で動く）
        ("pull_request:\n    types: [closed]", DOCS_ONLY, False),
        ("pull_request:\n    types: labeled", DOCS_ONLY, False),
        ("pull_request:\n    types: [opened, synchronize]", DOCS_ONLY, True),
        ("pull_request_target:\n    types: [labeled]", DOCS_ONLY, False),
        # pull_request_target の check run も PR の head の SHA に付く（#3672 の round 9。round 8 の判断を取り消した）
        ("pull_request_target:\n    types: [reopened, closed]", DOCS_ONLY, True),
        ("pull_request_target:", DOCS_ONLY, True),
        ("pull_request_target:\n    branches: [main]", DOCS_ONLY, True),
        ("pull_request_target:\n    paths: ['src/**']", DOCS_ONLY, False),
    ],
)
def test_runs_on_pull_head_matches_the_filters_against_the_pull(on, files, runs):
    """#3672 の round 5: 絞り込みを PR の事実（head のブランチ・宛先・変更ファイル）と照らし、読めない事実は動くとみなす。"""
    event = PullEvent("release/v1", "main", lambda: files, lambda: files)
    assert runs_on_pull_head(f"on:\n  {on}\n", event) is runs


def test_checks_failing_checks_required_source_app_before_admin_bypass(monkeypatch):
    """迂回の前に、宛先の必須のチェックを発行元まで照らす（同じ名前の別の発行元の成功で通さない。#3506）。
    検査の読み（check run と commit status）は 1 回で、必須のチェックの照合にも使う。"""
    import gh_checks

    port = land.GhPort(Path("."), "o/r", strategy("docs"))
    reads: list = []
    other = {"id": 1, "name": "ci", "status": "completed", "conclusion": "success", "app": {"id": 9}}
    runs = {"v": [other]}

    def fetch(repo, sha, empty_ok=False):
        reads.append("check-runs")
        return runs["v"]

    replies = {
        ST: {"statuses": [{"context": approval_trail.STATUS_CONTEXT, "state": "success"}]},
        "repos/o/r/commits/h/statuses?per_page=100&page=1": [],
    }
    monkeypatch.setattr(gh_checks, "fetch_check_runs", fetch)
    monkeypatch.setattr(land.GhPort, "_rest", lambda self, path, method="GET", payload=None: reads.append(path) or replies.get(path))
    required = (("ci", 7), ("lint", None))
    # 同じ名前の別のアプリ（9）の成功では通さない
    assert "アプリ 7" in port.checks_failing("h", {"tip": "t"}, required)
    # 指定のアプリで通ったが、指定の無い必須のチェック lint がまだ先端に現れていない
    runs["v"] = [other, {**other, "id": 2, "app": {"id": 7}}]
    reads.clear()
    assert "lint" in port.checks_failing("h", {"tip": "t"}, required)
    assert reads == ["check-runs", ST]  # 必須のチェックの照合で検査を読み直さない
    # 承認の証跡の status を発行元の指定なしで必須にした宛先は、証跡の status が現れていれば通す
    assert port.checks_failing("h", {"tip": "t"}, (("ci", 7), (approval_trail.STATUS_CONTEXT, None))) is None
    # 指定のアプリの bot が付けた commit status でも満たせる
    runs["v"] = [other]
    replies["repos/o/r/commits/h/statuses?per_page=100&page=1"] = [
        {"id": 3, "context": "ci", "state": "success", "creator": {"login": "app7[bot]"}}
    ]
    replies["apps/app7"] = {"id": 7}
    assert port.checks_failing("h", {"tip": "t"}, (("ci", 7),)) is None
    # commit status を読めなければ止める
    replies["repos/o/r/commits/h/statuses?per_page=100&page=1"] = None
    assert "読めない" in port.checks_failing("h", {"tip": "t"}, (("ci", 7),))


def test_rule_facts_carry_required_checks_with_sources():
    """必須のチェックは ruleset の事実と同じ 1 回の読み（規則と従来の保護）から、発行元の指定まで引く。"""
    rules = [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci", "integration_id": 7}]}},
    ]
    info = {"protection": {"enabled": True, "required_status_checks": {"contexts": ["lint"], "checks": [{"context": "e2e", "app_id": -1}]}}}
    replies = {
        "repos/{owner}/{repo}/rules/branches/main": rules,
        "repos/{owner}/{repo}/branches/main": info,
        "repos/{owner}/{repo}/branches/main/protection": {},
    }
    got = release_case.rule_facts_from(lambda path: (replies.get(path), ""), "main", bypass=False)
    assert got.required == (("ci", 7), ("lint", None), ("e2e", None))
