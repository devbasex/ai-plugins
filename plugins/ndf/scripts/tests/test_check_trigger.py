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

FAKE_GH = r'''#!{py}
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
    elif "state" in a:
        print(json.dumps({{"state": st.get("states", {{}}).get(n, "OPEN")}}))
    sys.exit(0)
if a[:2] in (["pr", "edit"], ["pr", "close"]):
    sys.exit(0)
sys.exit(1)
'''

TRIGGERS = {"score": 3, "common_weight": 2, "lines": 1000, "escapes": 2, "hours": 24}
DECL = {
    "version": 1,
    "fast": {"enabled": True, "verify": "true"},
    "areas": [{"name": "駆動", "common": True, "paths": ["core/**"]},
              {"name": "文書", "paths": ["docs/*.md"]}],
    "boundary_paths": [],
}


def git(root: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "o" / "r.git"
    origin.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false"),
                 ("tag.gpgsign", "false")):
        git(root, "config", k, v)
    git(root, "remote", "add", "origin", str(origin))
    write_decl(root, TRIGGERS)
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop",
                                                             "production_branch": "main"}))
    (root / ".ndf" / "supervise.json").write_text(json.dumps({"version": 1, "release": {
        "form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}}))
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
    e.update(PATH=f"{bindir}{os.pathsep}{e['PATH']}", FAKE_GH_STATE=str(state),
             CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
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
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True,
                       env=env, cwd=root)
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
    merge_pr(repo, 11, "feat/a", {"core/x.py": 1})         # 共通層: 2 点
    code, out, _ = call(repo, env, "eval", "--id", "c")
    assert (code, out["status"], out["items"]) == (3, "stopped", [])
    assert out["metrics"]["score"] == 2 and out["metrics"]["prs"] == 1
    merge_pr(repo, 12, "feat/b", {"app/y.py": 1})          # 1 点で 3 点
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


def test_lines_fire_only_above_threshold(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99, "lines": 10})
    git(repo, "commit", "-qam", "decl")
    git(repo, "tag", "-a", "ndf--v1.0.1", "-m", "v1.0.1")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 5})
    assert call(repo, env, "eval")[0] == 3
    merge_pr(repo, 12, "feat/b", {"app/b.py": 6})
    code, out, _ = call(repo, env, "eval")
    assert code == 0 and out["items"][0]["trigger"] == "lines" and out["metrics"]["lines"] == 11


def test_escapes_fire_on_the_second_in_the_same_area(repo, env):
    write_decl(repo, {**TRIGGERS, "score": 99})
    git(repo, "commit", "-qam", "decl")
    git(repo, "tag", "-a", "ndf--v1.0.1", "-m", "v1.0.1")
    gh_set(env, files={"21": ["core/a.py"], "22": ["docs/x.md"], "23": ["core/b.py"]})
    assert call(repo, env, "escape", "--pr", "21", "--of", "11")[0] == 0
    assert call(repo, env, "escape", "--pr", "22")[0] == 0
    assert call(repo, env, "eval")[0] == 3                  # 領域ごとに 1 件ずつ
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
    append_event(env, repo, {"kind": "check", "at": iso(25), "id": "m-1", "from": head, "to": head,
                             "result": "merged", "pr": 5})
    assert call(repo, env, "eval")[0] == 3                  # 25 時間でも PR が無い
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    code, out, _ = call(repo, env, "eval")
    assert code == 0 and [i["trigger"] for i in out["items"]] == ["hours"]
    assert out["metrics"]["from"] == head


def test_hours_below_threshold_do_not_fire(repo, env):
    head = git(repo, "rev-parse", "HEAD")
    append_event(env, repo, {"kind": "check", "at": iso(1), "id": "m-1", "from": head, "to": head,
                             "result": "no_change"})
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
    d.mkdir(exist_ok=True)
    (d / "state.json").write_text(json.dumps({"log": log, "llm": {}}))
    return d


def test_prepare_points_check_base_at_from_even_if_left_over(repo, env, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    merge_pr(repo, 11, "feat/a", {"app/a.py": 1})
    git(repo, "branch", "check-base/m-1", "HEAD")           # 前の回の残り（別の点）
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
    p = subprocess.run([sys.executable, str(SCRIPT), "scope", "--id", "m-1", "--state", str(st), "--root",
                        str(repo)], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout.split() == ["core/lib", "docs", "app"]


def test_scope_without_check_json_returns_two(repo, env, tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "scope", "--id", "m-1", "--state", str(tmp_path / "none"),
                        "--root", str(repo)], capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_record_failed_keeps_from_and_removes_check_base(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"core/a.py": 1})
    merge_pr(repo, 12, "feat/b", {"app/b.py": 1})
    _, before, _ = call(repo, env, "eval", "--id", "m-1")
    st = state_dir(tmp_path, [{"id": "prepare", "exit": 0}, {"id": "test-all", "exit": 1},
                              {"id": "judge", "exit": 0}])
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    code, out, _ = call(repo, env, "record", "--id", "m-1", "--state", str(st), "--failed")
    assert (code, out["status"]) == (1, "stopped")
    row = events(env, "check")[-1]
    assert (row["result"], row["failed_at"]) == ("failed", "test-all")
    assert git(repo, "ls-remote", "origin", "refs/heads/check-base/m-1") == ""
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "check-base/m-1"],
                          capture_output=True).returncode != 0
    _, after, _ = call(repo, env, "eval", "--id", "m-1")
    assert after["metrics"]["from"] == before["metrics"]["from"]


def test_record_failed_closes_the_pr(repo, env, tmp_path):
    st = state_dir(tmp_path, [{"id": "review", "exit": 1}])
    call(repo, env, "prepare", "--id", "m-1", "--state", str(st))
    call(repo, env, "record", "--id", "m-1", "--state", str(st), "--failed", "--pr", "30")
    assert ["pr", "close", "30"] == gh_calls(env)[-1][:3]


def test_record_merged_moves_the_start_of_the_next_range(repo, env, tmp_path):
    merge_pr(repo, 11, "feat/a", {"core/a.py": 1})
    st = state_dir(tmp_path, [{"id": "refactor", "exit": 0, "counts": {"adopted": 2, "reverted": 1}},
                              {"id": "review", "exit": 0, "counts": {"findings": 4, "unresolved": 0}}])
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


def test_review_and_final_are_exclusive(repo, env):
    assert call(repo, env, "eval", "--review", "--final")[0] == 2


def test_changed_reads_no_change_skipped_and_missing(repo, env, tmp_path):
    assert call(repo, env, "changed", "--id", "none")[0] == 2
    call(repo, env, "eval", "--id", "quiet", "--final")      # 範囲が空で立たない
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
