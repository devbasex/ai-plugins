"""check-trigger.py: 検査のトリガーの判定・範囲の用意と後始末・検査の記録（#1078）。

一時の git リポジトリ（origin は bare）にマージコミットを積み、トリガーを 1 つずつ閾値の上下で
立てる・立てない。gh は PATH の先頭に置いた偽物で置き換える。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "check-trigger.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

FAKE_GH = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
if a[:2] == ["pr", "view"]:
    n = a[2]
    if "files" in a:
        print(json.dumps({{"files": [{{"path": p}} for p in st.get("files", {{}}).get(n, [])]}}))
    elif "state,mergeCommit,baseRefName" in a:
        sha = st.get("merges", {{}}).get(n)
        print(json.dumps({{"state": st.get("states", {{}}).get(n, "OPEN"), "baseRefName": "develop", "mergeCommit": {{"oid": sha}} if sha else None}}))
    elif "state" in a:
        print(json.dumps({{"state": st.get("states", {{}}).get(n, "OPEN")}}))
    sys.exit(0)
if a[:2] == ["api", "--paginate"]:  # pulls/<n>/files を 2 ページに分けて返す
    rows = [{{"filename": p}} for p in st.get("files", {{}}).get(a[2].split("/")[-2], [])]
    print(json.dumps(rows[:1]) + json.dumps(rows[1:]))
    sys.exit(0)
if a[:2] == ["api", "graphql"]:
    import re
    q = next(x for x in a if x.startswith("query="))
    heads = st.get("heads", {{}})
    repo = {{f"p{{n}}": {{"headRefName": heads.get(n, "feat/x")}} for n in re.findall(r"p(\d+): pullRequest", q)}}
    print(json.dumps({{"data": {{"repository": repo}}}}))
    sys.exit(0)
if a[:2] in (["pr", "edit"], ["pr", "close"]):
    sys.exit(0)
sys.exit(1)
"""

TRIGGERS = {"score": 3, "common_weight": 2, "lines": 1000, "escapes": 2, "hours": 24}
DECL = {
    "version": 1,
    "fast": {"enabled": True, "verify": "true"},
    "areas": [{"name": "駆動", "common": True, "paths": ["core/**"]}, {"name": "文書", "paths": ["docs/*.md"]}],
    "boundary_paths": [],
}


def git(root: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "o" / "r.git"
    origin.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        git(root, "config", k, v)
    git(root, "remote", "add", "origin", str(origin))
    write_decl(root, TRIGGERS)
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    (root / ".ndf" / "supervise.json").write_text(
        json.dumps({"version": 1, "release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}})
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "tag", "-a", "ndf--v1.0.0", "-m", "v1.0.0")
    git(root, "push", "-q", "origin", "develop", "--tags")
    return root


def write_decl(root: Path, triggers: dict) -> None:
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "pace.json").write_text(json.dumps({**DECL, "triggers": triggers}, ensure_ascii=False))


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(py=sys.executable))
    gh.chmod(0o755)
    state = tmp_path / "gh-state.json"
    state.write_text("{}")
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update(PATH=f"{bindir}{os.pathsep}{e['PATH']}", FAKE_GH_STATE=str(state), CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    return e


def gh_set(env: dict, **kw) -> None:
    Path(env["FAKE_GH_STATE"]).write_text(json.dumps(kw))


def gh_calls(env: dict) -> list[list[str]]:
    return json.loads(Path(env["FAKE_GH_STATE"]).read_text()).get("calls", [])


def merge_pr(root: Path, n: int, branch: str, files: dict[str, int]) -> str:
    """ブランチで files（パス → 行数）を書き、develop へ Pull Request のマージの形で入れて送る。"""
    git(root, "checkout", "-q", "-b", branch)
    for path, lines in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"{branch} {i}\n" for i in range(lines)))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"{branch} の変更")
    git(root, "checkout", "-q", "develop")
    git(root, "merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{n} from o/{branch}")
    git(root, "push", "-q", "origin", "develop")
    return git(root, "rev-parse", "HEAD")


def call(root: Path, env: dict, *args: str) -> tuple[int, dict | None, str]:
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True, env=env, cwd=root)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stdout + p.stderr


def events(env: dict, kind: str | None = None) -> list[dict]:
    files = list((Path(env["CLAUDE_PLUGIN_DATA"]) / "checks").glob("*.jsonl"))
    rows = [json.loads(ln) for f in files for ln in f.read_text().splitlines()]
    return [r for r in rows if kind is None or r["kind"] == kind]


def append_event(env: dict, root: Path, row: dict) -> None:
    d = Path(env["CLAUDE_PLUGIN_DATA"]) / "checks"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "o__r.jsonl").open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).isoformat(timespec="seconds")


# ---------- トリガー: 立つ・立たない ----------


def test_score_counts_common_layer_double_and_fires_at_threshold(repo, env):
    merge_pr(repo, 11, "feat/a", {"core/x.py": 1})  # 共通層: 2 点
    code, out, _ = call(repo, env, "eval", "--id", "c")
    assert (code, out["status"], out["items"]) == (3, "stopped", [])
    assert out["metrics"]["score"] == 2 and out["metrics"]["prs"] == 1
    merge_pr(repo, 12, "feat/b", {"app/y.py": 1})  # 1 点で 3 点
    code, out, _ = call(repo, env, "eval", "--id", "c")
    assert (code, out["status"]) == (0, "ok")
    assert out["items"] == [{"trigger": "score", "value": 3, "threshold": 3}]


def test_release_and_check_branches_are_not_counted(repo, env):
    merge_pr(repo, 11, "release/v1.1.0", {"core/x.py": 1})
    merge_pr(repo, 12, "check/m-1", {"core/y.py": 1})
    merge_pr(repo, 13, "fix/c", {"app/z.py": 1})
    code, out, _ = call(repo, env, "eval")
    assert out["metrics"]["prs"] == 1 and out["metrics"]["score"] == 1
    merges = git(repo, "log", "--first-parent", "--merges", "--format=%s", "ndf--v1.0.0..origin/develop")
    assert len(merges.splitlines()) == 3


def squash_pr(root: Path, n: int, title: str, files: dict[str, int]) -> str:
    """develop へ squash merge の形（1 コミット・件名の末尾が (#N)）で入れて送る。"""
    for path, lines in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"{title} {i}\n" for i in range(lines)))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"{title} (#{n})")
    git(root, "push", "-q", "origin", "develop")
    return git(root, "rev-parse", "HEAD")


def test_squash_merged_prs_are_counted_and_recorded_check_prs_are_not(repo, env):
    squash_pr(repo, 11, "feat: a", {"core/x.py": 1})
    squash_pr(repo, 12, "fix: b", {"app/y.py": 1})
    squash_pr(repo, 30, "検査: m-1", {"app/z.py": 1})
    append_event(env, repo, {"kind": "check", "at": iso(5), "id": "m-1", "from": "", "to": "", "result": "failed", "pr": 30})
    code, out, _ = call(repo, env, "eval", "--review")
    assert code == 0 and out["metrics"]["prs"] == 2
    _, out, _ = call(repo, env, "eval")
    assert out["metrics"]["score"] == 3


def test_squash_merged_release_and_check_branches_are_not_counted(repo, env):
    squash_pr(repo, 11, "release: v1.1.0", {"core/x.py": 1})
    squash_pr(repo, 12, "検査: m-2", {"core/y.py": 1})
    squash_pr(repo, 13, "fix: c", {"app/z.py": 1})
    gh_set(env, heads={"11": "release/v1.1.0", "12": "check/m-2", "13": "fix/c"})
    code, out, _ = call(repo, env, "eval")
    assert out["metrics"]["prs"] == 1 and out["metrics"]["score"] == 1
    assert [c[:2] for c in gh_calls(env)] == [["api", "graphql"]]  # PR 1 本ごとに呼ばない


def test_lines_fire_only_above_threshold(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99, "lines": 10})
    git(repo, "commit", "-qam", "decl")
    git(repo, "tag", "-a", "ndf--v1.0.1", "-m", "v1.0.1")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 5})
    assert call(repo, env, "eval")[0] == 3
    merge_pr(repo, 12, "feat/b", {"app/b.py": 6})
    code, out, _ = call(repo, env, "eval")
    assert code == 0 and out["items"][0]["trigger"] == "lines" and out["metrics"]["lines"] == 11


def test_lines_of_check_and_release_prs_are_not_counted(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99, "lines": 10})
    git(repo, "commit", "-qam", "decl")
    git(repo, "tag", "-a", "ndf--v1.0.1", "-m", "v1.0.1")
    merge_pr(repo, 11, "check/m-1", {"app/a.py": 20})  # 検査の修正
    squash_pr(repo, 12, "release: v1.1.0", {"app/b.py": 20})
    squash_pr(repo, 30, "検査: m-2", {"app/c.py": 20})
    append_event(env, repo, {"kind": "check", "at": iso(1), "id": "m-2", "from": "", "to": "", "result": "failed", "pr": 30})
    gh_set(env, heads={"12": "release/v1.1.0"})
    merge_pr(repo, 13, "feat/a", {"app/d.py": 3})
    code, out, _ = call(repo, env, "eval")
    assert code == 3 and out["metrics"]["lines"] == 3 and out["metrics"]["prs"] == 1


def test_escapes_fire_on_the_second_in_the_same_area(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99})
    git(repo, "commit", "-qam", "decl")
    git(repo, "tag", "-a", "ndf--v1.0.1", "-m", "v1.0.1")
    gh_set(env, files={"21": ["core/a.py"], "22": ["docs/x.md"], "23": ["core/b.py"]})
    assert call(repo, env, "escape", "--pr", "21", "--of", "11")[0] == 0
    assert call(repo, env, "escape", "--pr", "22")[0] == 0
    assert call(repo, env, "eval")[0] == 3  # 領域ごとに 1 件ずつ
    call(repo, env, "escape", "--pr", "23", "--of", "0")
    code, out, _ = call(repo, env, "eval")
    assert code == 0 and out["items"][0] == {"trigger": "escapes", "value": 2, "threshold": 2}
    esc = events(env, "escape")
    assert [e["areas"] for e in esc] == [["駆動"], ["文書"], ["駆動"]]
    assert [e["of"] for e in esc] == [11, 0, 0]


def test_hours_fire_only_with_a_pr_in_the_range(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99})
    git(repo, "commit", "-qam", "decl")
    git(repo, "push", "-q", "origin", "develop")
    head = git(repo, "rev-parse", "HEAD")
    append_event(env, repo, {"kind": "check", "at": iso(25), "id": "m-1", "from": head, "to": head, "result": "merged", "pr": 5})
    assert call(repo, env, "eval")[0] == 3  # 25 時間でも PR が無い
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    code, out, _ = call(repo, env, "eval")
    assert code == 0 and [i["trigger"] for i in out["items"]] == ["hours"]
    assert out["metrics"]["from"] == head


def test_hours_below_threshold_do_not_fire(repo, env):
    head = git(repo, "rev-parse", "HEAD")
    append_event(env, repo, {"kind": "check", "at": iso(1), "id": "m-1", "from": head, "to": head, "result": "no_change"})
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    assert call(repo, env, "eval")[0] == 3


def test_final_fires_with_a_pr_and_skips_an_empty_range(repo, env):
    assert call(repo, env, "eval", "--final")[0] == 3
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    code, out, _ = call(repo, env, "eval", "--final")
    assert code == 0 and [i["trigger"] for i in out["items"]] == ["final"]


def test_broken_declaration_returns_two(repo, env):
    (repo / ".ndf" / "pace.json").write_text("{not json")
    code, out, _ = call(repo, env, "eval")
    assert code == 2 and out["status"] == "stopped"


def test_missing_declaration_returns_two(repo, env):
    (repo / ".ndf" / "pace.json").unlink()
    assert call(repo, env, "eval")[0] == 2


def test_every_eval_is_recorded_fired_or_not(repo, env):
    call(repo, env, "eval", "--id", "a")
    merge_pr(repo, 11, "feat/a", {"core/a.py": 1})
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    call(repo, env, "eval", "--id", "b")
    rows = events(env, "eval")
    assert [(r["id"], r["fired"]) for r in rows] == [("a", []), ("b", ["score"])]
    assert rows[1]["to"] == git(repo, "rev-parse", "origin/develop")


def test_range_starts_at_the_latest_release_tag_not_a_prerelease(repo, env):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    git(repo, "tag", "-a", "ndf--v1.1.0", "-m", "v1.1.0")
    tagged = git(repo, "rev-parse", "HEAD")
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    git(repo, "tag", "-a", "ndf--v1.2.0-dev.1", "-m", "dev")
    code, out, _ = call(repo, env, "eval")
    assert out["metrics"]["from"] == tagged and out["metrics"]["prs"] == 1


def test_since_overrides_the_tag(repo, env):
    first = git(repo, "rev-parse", "HEAD")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    git(repo, "tag", "-a", "ndf--v1.1.0", "-m", "v1.1.0")
    code, out, _ = call(repo, env, "eval", "--since", first)
    assert out["metrics"]["from"] == first and out["metrics"]["prs"] == 1


# ---------- 範囲の用意・後始末・記録 ----------


def state_dir(tmp_path: Path, log: list[dict]) -> Path:
    d = tmp_path / "plan-state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"log": log, "llm": {}}))
    return d


def test_prepare_points_check_base_at_from_even_if_left_over(repo, env, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    git(repo, "branch", "check-base/m-1", "HEAD")  # 前の回の残り（別の点）
    git(repo, "push", "-q", "origin", "check-base/m-1")
    st = state_dir(tmp_path, [])
    code, out, _ = call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    assert code == 0, out
    assert git(repo, "rev-parse", "origin/check-base/m-1") == base
    assert git(repo, "ls-remote", "origin", "refs/heads/check-base/m-1").split()[0] == base
    check = json.loads((st / "check.json").read_text())
    assert check["from"] == base and check["files"] == ["app/a.py"]


def test_scope_orders_common_then_escaped_then_others(repo, env, tmp_path):
    gh_set(env, files={"21": ["docs/x.md"]})
    call(repo, env, "escape", "--pr", "21")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1, "docs/x.md": 1, "core/lib/c.py": 1})
    st = state_dir(tmp_path, [])
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "scope", "--id", "m-1", "--state", str(st), "--root", str(repo)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0 and p.stdout.split() == ["core/lib", "docs", "app"]


def test_scope_without_check_json_returns_two(repo, env, tmp_path):
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "scope", "--id", "m-1", "--state", str(tmp_path / "none"), "--root", str(repo)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 2


def test_record_failed_keeps_from_and_removes_check_base(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"core/a.py": 1})
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    _, before, _ = call(repo, env, "eval", "--id", "m-1")
    st = state_dir(tmp_path, [{"id": "prepare", "exit": 0}, {"id": "test-all", "exit": 1}, {"id": "judge", "exit": 0}])
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    code, out, _ = call(repo, env, "record", "--id", "m-1", "--state", str(st), "--failed")
    assert (code, out["status"]) == (1, "stopped")
    row = events(env, "check")[-1]
    assert (row["result"], row["failed_at"]) == ("failed", "test-all")
    assert git(repo, "ls-remote", "origin", "refs/heads/check-base/m-1") == ""
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "check-base/m-1"], capture_output=True).returncode != 0
    _, after, _ = call(repo, env, "eval", "--id", "m-1")
    assert after["metrics"]["from"] == before["metrics"]["from"]


def test_record_failed_closes_the_pr(repo, env, tmp_path):
    st = state_dir(tmp_path, [{"id": "review", "exit": 1}])
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    call(repo, env, "record", "--id", "m-1", "--state", str(st), "--failed", "--pr", "30")
    assert ["pr", "close", "30"] == gh_calls(env)[-1][:3]


def test_record_merged_moves_the_start_of_the_next_range(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"core/a.py": 1})
    st = state_dir(
        tmp_path,
        [
            {"id": "refactor", "exit": 0, "counts": {"adopted": 2, "reverted": 1}},
            {"id": "review", "exit": 0, "counts": {"findings": 4, "unresolved": 0}},
        ],
    )
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    to = json.loads((st / "check.json").read_text())["to"]
    gh_set(env, states={"30": "MERGED"})
    code, out, _ = call(repo, env, "record", "--id", "m-1", "--pr", "30", "--state", str(st))
    assert code == 0, out
    row = events(env, "check")[-1]
    assert row["result"] == "merged" and row["pr"] == 30
    assert row["findings"] == {"applied": 2, "reverted": 1, "findings": 4, "unresolved": 0}
    _, nxt, _ = call(repo, env, "eval")
    assert nxt["metrics"]["from"] == to and nxt["metrics"]["prs"] == 0
    assert call(repo, env, "changed", "--id", "m-1")[0] == 0


def test_review_fires_on_one_pr_without_the_other_triggers(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99, "lines": 10_000})
    assert call(repo, env, "eval", "--review")[0] == 3
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    assert call(repo, env, "eval")[0] == 3
    code, out, _ = call(repo, env, "eval", "--review")
    assert code == 0 and out["items"] == [{"trigger": "review", "value": 1, "threshold": 1}]


def test_review_only_record_moves_the_review_range_but_not_the_check_range(repo, env, tmp_path):
    first = merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    st = state_dir(tmp_path, [{"id": "review", "exit": 0, "counts": {"findings": 1, "unresolved": 0}}])
    call(repo, env, "prepare", "--id", "m-review", "--state", str(st), "--review")
    gh_set(env, states={"31": "MERGED"})
    code, out, _ = call(repo, env, "record", "--id", "m-review", "--pr", "31", "--state", str(st), "--review")
    assert code == 0, out
    assert events(env, "check")[-1]["only"] == "review"
    code, rv, _ = call(repo, env, "eval", "--review")
    assert code == 3 and rv["metrics"]["from"] == first and rv["metrics"]["prs"] == 0
    _, full, _ = call(repo, env, "eval")
    assert full["metrics"]["from"] != first and full["metrics"]["prs"] == 1


def test_a_full_check_also_starts_the_next_review_range(repo, env, tmp_path):
    to = merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    append_event(env, repo, {"kind": "check", "at": iso(0), "id": "m-1", "from": "", "to": to, "result": "merged"})
    code, rv, _ = call(repo, env, "eval", "--review")
    assert code == 3 and rv["metrics"]["from"] == to


def test_stats_counts_escapes_until_the_next_record_of_the_same_kind(repo, env):
    append_event(env, repo, {"kind": "check", "at": iso(3), "id": "m-1", "result": "merged"})
    append_event(env, repo, {"kind": "check", "at": iso(2), "id": "m-r", "result": "merged", "only": "review"})
    append_event(env, repo, {"kind": "escape", "at": iso(1), "pr": 21})
    code, out, _ = call(repo, env, "stats")
    rows = {r["id"]: r for r in out["items"]}
    assert code == 0 and rows["m-1"]["escapes_after"] == 1 and rows["m-r"]["escapes_after"] == 1
    assert rows["m-1"]["only"] is None and rows["m-r"]["only"] == "review"


def test_stats_closes_a_review_only_range_at_the_next_full_check(repo, env):
    append_event(env, repo, {"kind": "check", "at": iso(3), "id": "m-r", "result": "merged", "only": "review"})
    append_event(env, repo, {"kind": "check", "at": iso(2), "id": "m-1", "result": "merged"})
    append_event(env, repo, {"kind": "escape", "at": iso(1), "pr": 21})
    code, out, _ = call(repo, env, "stats")
    rows = {r["id"]: r for r in out["items"]}
    assert code == 0 and rows["m-r"]["escapes_after"] == 0 and rows["m-1"]["escapes_after"] == 1


# ---------- PR を指す検査の記録と、流出不具合の結び付け（#1317） ----------

REVIEW = {"rounds": 4, "comments": 7, "findings": 5, "fixed": 5, "deferred": 0, "rejected": 0, "unresolved": 0}


def test_a_check_of_one_pr_is_recorded_and_does_not_move_the_range(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    _, before, _ = call(repo, env, "eval")
    st = state_dir(tmp_path, [{"id": "refactor", "exit": 0, "counts": {"adopted": 1}}, {"id": "review", "exit": 0, "counts": REVIEW}])
    gh_set(env, states={"11": "MERGED"})
    calls = len(gh_calls(env))
    code, out, _ = call(repo, env, "record", "--id", "pr-11", "--target-pr", "11", "--state", str(st))
    assert code == 0, out
    row = events(env, "check")[-1]
    assert (row["scope"], row["target_pr"], row["prs"], row["result"]) == ("pr", 11, [11], "merged")
    assert "pr" not in row and not row["to"]
    # 件数はフェーズレポートと同じ state.json の counts から写す
    assert {k: row["findings"][k] for k in REVIEW} == REVIEW and row["findings"]["applied"] == 1
    assert all(c[:2] != ["pr", "close"] for c in gh_calls(env)[calls:])
    _, after, _ = call(repo, env, "eval")
    assert (after["metrics"]["from"], after["metrics"]["prs"]) == (before["metrics"]["from"], before["metrics"]["prs"])
    code, st_out, _ = call(repo, env, "stats")
    item = next(r for r in st_out["items"] if r["id"] == "pr-11")
    assert (item["scope"], item["target_pr"], item["result"], item["findings"]["findings"]) == ("pr", 11, "merged", 5)
    assert st_out["metrics"]["checks_pr"] == 1


def test_a_failed_check_of_one_pr_is_recorded_without_closing_it(repo, env, tmp_path):
    st = state_dir(tmp_path, [{"id": "review", "exit": 1}])
    code, out, _ = call(repo, env, "record", "--id", "pr-12", "--target-pr", "12", "--failed", "--state", str(st))
    assert (code, out["status"]) == (1, "stopped")
    row = events(env, "check")[-1]
    assert (row["scope"], row["result"], row["failed_at"]) == ("pr", "failed", "review")
    assert all(c[:2] != ["pr", "close"] for c in gh_calls(env))
    assert call(repo, env, "stats")[1]["metrics"]["failed"] == 1


def done_sha(repo: Path, name: str) -> str:
    out = git(repo, "ls-remote", "origin", f"refs/heads/check-done/{name}")
    return out.split()[0] if out else ""


def test_a_sprint_check_moves_check_done_to_the_merge_commit_of_its_pr(repo, env, tmp_path):
    """#1272: スプリントの検査（--advance-done）はマージしたスプリント PR のマージのコミットへ check-done/* を進め、
    次の差分の検査の範囲にスプリント PR の差分が入らない。"""
    merge_pr(repo, 11, "feat/before", {"app/a.py": 1})
    sprint = merge_pr(repo, 40, "sprint/m1", {"core/s.py": 3})
    st = state_dir(tmp_path, [{"id": "review", "exit": 0, "counts": REVIEW}])
    gh_set(env, states={"40": "MERGED"}, merges={"40": sprint})
    code, out, _ = call(repo, env, "record", "--id", "check", "--target-pr", "40", "--advance-done", "--state", str(st))
    assert code == 0, out
    assert out["metrics"]["pushed"] == ["check-done/review", "check-done/check"]
    assert done_sha(repo, "review") == sprint and done_sha(repo, "check") == sprint
    assert events(env, "check")[-1]["to"] == sprint
    git(repo, "fetch", "-q", "origin")
    merge_pr(repo, 41, "feat/after", {"docs/x.md": 1})
    st2 = state_dir(tmp_path / "next", [])
    assert call(repo, env, "prepare", "--id", "m-2", "--state", str(st2))[0] == 0
    assert json.loads((st2 / "check.json").read_text())["files"] == ["docs/x.md"]


@pytest.mark.parametrize("state, failed", [("MERGED", True), ("CLOSED", False)])
def test_a_failed_or_unmerged_sprint_check_does_not_move_check_done(repo, env, tmp_path, state, failed):
    sprint = merge_pr(repo, 40, "sprint/m1", {"core/s.py": 3})
    st = state_dir(tmp_path, [{"id": "review", "exit": 1}])
    gh_set(env, states={"40": state}, merges={"40": sprint})
    args = ["record", "--id", "check", "--target-pr", "40", "--advance-done", "--state", str(st)] + (["--failed"] if failed else [])
    code, out, _ = call(repo, env, *args)
    assert code == (1 if failed else 0), out
    assert done_sha(repo, "check") == "" and done_sha(repo, "review") == ""
    row = events(env, "check")[-1]
    assert not row["to"] and row["result"] == ("failed" if failed else "no_change")


def test_advance_done_needs_target_pr(repo, env, tmp_path):
    st = state_dir(tmp_path, [])
    assert call(repo, env, "record", "--id", "x", "--pr", "30", "--advance-done", "--state", str(st))[0] == 2


def test_target_pr_cannot_be_combined_with_pr(repo, env, tmp_path):
    st = state_dir(tmp_path, [])
    assert call(repo, env, "record", "--id", "x", "--target-pr", "12", "--pr", "30", "--state", str(st))[0] == 2
    assert events(env, "check") == []


def test_a_since_check_records_the_prs_of_its_range(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    record_merged(repo, env, tmp_path, "m-1", 30)
    row = events(env, "check")[-1]
    assert (row["scope"], sorted(row["prs"]), row["pr"]) == ("since", [11, 12], 30)


def test_stats_links_an_escape_to_the_last_check_that_had_its_pr_in_range(repo, env):
    append_event(env, repo, {"kind": "check", "at": iso(6), "id": "old", "result": "merged"})
    append_event(env, repo, {"kind": "check", "at": iso(5), "id": "m-1", "result": "merged", "scope": "since", "prs": [11, 12]})
    append_event(env, repo, {"kind": "check", "at": iso(4), "id": "pr-12", "result": "merged", "scope": "pr", "target_pr": 12, "prs": [12]})
    esc = [
        {"kind": "escape", "at": iso(3), "pr": 21, "of": 12},
        {"kind": "escape", "at": iso(3), "pr": 22, "of": 11},
        {"kind": "escape", "at": iso(2), "pr": 23, "of": 0},
        {"kind": "escape", "at": iso(2), "pr": 24, "of": 99},
    ]
    for e in esc:
        append_event(env, repo, e)
    code, out, _ = call(repo, env, "stats")
    rows = {r["id"]: r for r in out["items"]}
    assert code == 0 and rows["pr-12"]["escapes_linked"] == [21] and rows["m-1"]["escapes_linked"] == [22]
    m = out["metrics"]
    assert (m["escapes_linked"], m["escapes_unlinked"]) == (2, 2)
    # 範囲を持たない古い行があるので、of を含む検査が無い流出は no_range
    assert m["unlinked"] == [{"pr": 23, "of": 0, "reason": "of_unknown"}, {"pr": 24, "of": 99, "reason": "no_range"}]
    # PR を指す検査は時刻の窓を切らない
    assert "escapes_after" not in rows["pr-12"] and rows["m-1"]["escapes_after"] == 4
    assert [(e["pr"], e["of"]) for e in events(env, "escape")] == [(21, 12), (22, 11), (23, 0), (24, 99)]  # 流出の行は変えない


def test_stats_says_no_check_when_no_check_had_the_pr(repo, env):
    append_event(env, repo, {"kind": "check", "at": iso(5), "id": "m-1", "result": "merged", "scope": "since", "prs": [11]})
    append_event(env, repo, {"kind": "escape", "at": iso(2), "pr": 21, "of": 12})
    assert call(repo, env, "stats")[1]["metrics"]["unlinked"] == [{"pr": 21, "of": 12, "reason": "no_check"}]


def test_stats_reads_the_findings_of_an_old_row_as_comments(repo, env):
    append_event(env, repo, {"kind": "check", "at": iso(2), "id": "r28", "result": "merged", "findings": {"findings": 6, "unresolved": 0}})
    f = call(repo, env, "stats")[1]["items"][0]["findings"]
    assert (f["comments"], f["findings"], f["fixed"], f["unresolved"]) == (6, None, None, 0)
    assert events(env, "check")[0]["findings"] == {"findings": 6, "unresolved": 0}  # 行は書き換えない


def record_merged(repo, env, tmp_path, name, pr, *flags):
    st = state_dir(tmp_path / name, [])
    call(repo, env, "prepare", "--id", name, "--state", str(st), *flags)
    gh_set(env, states={str(pr): "MERGED"})
    code, out, _ = call(repo, env, "record", "--id", name, "--pr", str(pr), "--state", str(st), *flags)
    assert code == 0, out
    return json.loads((st / "check.json").read_text())["to"], out


def forget_local_records(env):
    for f in (Path(env["CLAUDE_PLUGIN_DATA"]) / "checks").glob("*.jsonl"):
        f.unlink()


def test_a_lost_local_record_still_starts_at_the_last_check_on_origin(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    to, out = record_merged(repo, env, tmp_path, "m-1", 30)
    assert out["metrics"]["pushed"] == ["check-done/review", "check-done/check"]
    assert git(repo, "rev-parse", "origin/check-done/check") == to
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    forget_local_records(env)
    _, full, _ = call(repo, env, "eval")
    _, rv, _ = call(repo, env, "eval", "--review")
    assert full["metrics"]["from"] == rv["metrics"]["from"] == to and full["metrics"]["prs"] == 1


def test_review_only_moves_only_the_review_branch_on_origin(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    to, out = record_merged(repo, env, tmp_path, "m-review", 31, "--review")
    assert out["metrics"]["pushed"] == ["check-done/review"]
    forget_local_records(env)
    _, rv, _ = call(repo, env, "eval", "--review")
    _, full, _ = call(repo, env, "eval")
    assert rv["metrics"]["from"] == to and full["metrics"]["from"] != to and full["metrics"]["prs"] == 1


def test_an_older_check_finishing_later_does_not_move_the_done_branch_back(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    old = state_dir(tmp_path / "old", [])
    call(repo, env, "prepare", "--id", "m-old", "--state", str(old))
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    new_to, _ = record_merged(repo, env, tmp_path, "m-new", 31)
    gh_set(env, states={"30": "MERGED"})
    code, out, _ = call(repo, env, "record", "--id", "m-old", "--pr", "30", "--state", str(old))
    assert code == 0 and out["metrics"]["pushed"] == ["check-done/review", "check-done/check"], out
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", "origin/check-done/check") == git(repo, "rev-parse", "origin/check-done/review") == new_to


def test_a_record_that_could_not_be_pushed_starts_the_next_range(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    old_to, _ = record_merged(repo, env, tmp_path, "m-1", 30)
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    st = state_dir(tmp_path / "m-2", [])
    call(repo, env, "prepare", "--id", "m-2", "--state", str(st))
    git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "gone.git"))  # 送れない
    gh_set(env, states={"31": "MERGED"})
    code, out, _ = call(repo, env, "record", "--id", "m-2", "--pr", "31", "--state", str(st))
    new_to = json.loads((st / "check.json").read_text())["to"]
    assert code == 0 and out["metrics"]["unpushed"] == ["check-done/review", "check-done/check"], out
    assert git(repo, "rev-parse", "origin/check-done/check") == old_to != new_to
    _, full, _ = call(repo, env, "eval")
    assert full["metrics"]["from"] == new_to and full["metrics"]["prs"] == 0


def test_a_failed_check_leaves_the_branch_on_origin(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    st = state_dir(tmp_path / "f", [{"id": "review", "exit": 1}])
    call(repo, env, "prepare", "--id", "m-2", "--state", str(st))
    call(repo, env, "record", "--id", "m-2", "--state", str(st), "--failed")
    assert (
        subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "origin/check-done/review"], capture_output=True).returncode
        != 0
    )


def test_review_and_final_are_exclusive(repo, env):
    assert call(repo, env, "eval", "--review", "--final")[0] == 2


def test_changed_reads_no_change_skipped_and_missing(repo, env, tmp_path):
    assert call(repo, env, "changed", "--id", "none")[0] == 2
    call(repo, env, "eval", "--id", "quiet", "--final")  # 範囲が空で立たない
    assert call(repo, env, "changed", "--id", "quiet")[0] == 3
    st = state_dir(tmp_path, [])
    call(repo, env, "prepare", "--id", "m-2", "--state", str(st))
    gh_set(env, states={"31": "CLOSED"})
    call(repo, env, "record", "--id", "m-2", "--pr", "31", "--state", str(st))
    assert events(env, "check")[-1]["result"] == "no_change"
    assert call(repo, env, "changed", "--id", "m-2")[0] == 3


def test_finish_closes_a_pr_without_changes_and_retargets_one_with(repo, env):
    git(repo, "checkout", "-q", "-b", "check/m-1")
    code, out, _ = call(repo, env, "finish", "--id", "m-1", "--pr", "30")
    assert (code, out["status"]) == (3, "stopped")
    assert gh_calls(env)[-1][:3] == ["pr", "close", "30"]
    (repo / "app.py").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "直した")
    code, out, _ = call(repo, env, "finish", "--id", "m-1", "--pr", "30")
    assert code == 0
    assert gh_calls(env)[-1] == ["pr", "edit", "30", "--base", "develop"]
