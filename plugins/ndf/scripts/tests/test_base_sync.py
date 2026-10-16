"""ベースブランチの取り込み（merged-steps.py base-sync / base-return）と、プランの取り込みの経路（#1917 #2003）。

取り込みは一時リポジトリ（origin は bare）で確かめ、プランの経路は雛形の書き出しとスタブのエンジンで確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402
from supervise_lib import engine, limits  # noqa: E402
from supervise_lib.procedures import base_sync_steps  # noqa: E402
from test_glossary import ADDED_OURS, ADDED_THEIRS, shop_glossary, write_json  # noqa: E402
from test_glossary import run as glossary_run  # noqa: E402
from test_supervise_limits import MATCH, _route, drive, reason_of, scripted  # noqa: E402
from test_supervise_limits import plans as limit_plans  # noqa: E402,F401  雛形の書き出し（1 回だけ）

PY = sys.executable
MERGED = SCRIPTS / "merged-steps.py"
SOURCE, DOCUMENT = "docs/glossary/glossary.json", "docs/glossary.md"


@pytest.fixture()
def tpl(request):
    """test_supervise_limits の雛形の書き出し（limit_plans。1 回だけ書き出す）。"""
    return request.getfixturevalue("limit_plans")


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check).stdout


def commit(root, msg):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def configure(root):
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)


class Repos:
    """origin（bare）・ベースブランチを進める up・作業ブランチ feat/x の wt。"""

    def __init__(self, tmp: Path, files: dict[str, str] | None = None, glossary: bool = False):
        self.origin, self.up, self.wt = tmp / "origin.git", tmp / "up", tmp / "wt"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.up)], check=True, capture_output=True)
        configure(self.up)
        git(self.up, "checkout", "-q", "-b", "develop")
        for rel, text in {"README.md": "x\n", **(files or {})}.items():
            self.write(self.up, rel, text)
        if glossary:
            write_json(self.up, ".ndf/glossary.json", {"version": 1, "format": "json", "source": SOURCE, "document": DOCUMENT})
            write_json(self.up, SOURCE, shop_glossary())
            assert glossary_run(self.up, "render")[0] == 0
        commit(self.up, "init")
        git(self.up, "push", "-q", "origin", "develop")
        subprocess.run(["git", "clone", "-q", "-b", "develop", str(self.origin), str(self.wt)], check=True, capture_output=True)
        configure(self.wt)
        git(self.wt, "checkout", "-q", "-b", "feat/x")
        git(self.wt, "push", "-q", "-u", "origin", "feat/x")

    @staticmethod
    def write(root, rel, text):
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def advance(self, rel, text, msg="base"):
        """ベースブランチ（develop）を進める。"""
        self.write(self.up, rel, text)
        commit(self.up, msg)
        git(self.up, "push", "-q", "origin", "develop")

    def work(self, rel, text, msg="work"):
        self.write(self.wt, rel, text)
        commit(self.wt, msg)

    def head(self):
        return git(self.wt, "rev-parse", "HEAD").strip()

    def remote_head(self):
        return git(self.origin, "rev-parse", "feat/x").strip()

    def merging(self):
        return subprocess.run(["git", "-C", str(self.wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0


def merged(args, cwd, env=None):
    p = subprocess.run([PY, str(MERGED), *args], capture_output=True, text=True, cwd=cwd, env=env)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stderr


def base_sync(r: Repos, *extra, env=None):
    return merged(["base-sync", "--base", "develop", *extra, "--root", str(r.wt)], r.wt, env)


def results(out):
    return [(i["kind"], i["result"]) for i in out["items"]]


# --- base-sync -----------------------------------------------------------------


def test_2003_5_nothing_to_take_makes_no_commit_and_no_push(tmp_path):
    r = Repos(tmp_path)
    before = (r.head(), r.remote_head())
    code, out, err = base_sync(r, "--push")
    assert code == 0, (out, err)
    assert results(out) == [("base", "up_to_date")] and out["metrics"]["pushed"] is False
    assert (r.head(), r.remote_head()) == before


def test_clean_take_merges_with_a_commit_and_pushes_only_with_push(tmp_path):
    r = Repos(tmp_path)
    r.work("mine.txt", "mine\n")
    r.advance("theirs.txt", "theirs\n")
    code, out, err = base_sync(r)
    assert code == 0 and results(out) == [("base", "merged")], (out, err)
    assert len(git(r.wt, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3  # マージのコミット
    assert (r.wt / "theirs.txt").exists() and r.remote_head() != r.head()  # --push が無ければ送らない
    code, out, err = base_sync(r, "--push")  # 進んでいないが、upstream より進んでいるので送る（I4）
    assert code == 0 and results(out) == [("base", "up_to_date"), ("push", "pushed")], (out, err)
    assert r.remote_head() == r.head()


def test_i3_no_rebase_and_no_force(tmp_path):
    r = Repos(tmp_path)
    r.work("mine.txt", "mine\n")
    r.advance("theirs.txt", "theirs\n")
    bindir, log = tmp_path / "bin", tmp_path / "git.log"
    bindir.mkdir()
    shim = bindir / "git"
    shim.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexec {shutil.which("git")} "$@"\n')
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    code, out, err = base_sync(r, "--push", env=env)
    assert code == 0 and ("push", "pushed") in results(out), (out, err)
    calls = log.read_text().splitlines()
    assert any(" merge " in f" {c} " for c in calls)
    assert not [c for c in calls if "rebase" in c.split() or "--force" in c or " -f" in f" {c}"]


def test_1917_2_glossary_only_conflict_is_solved_without_a_worker(tmp_path):
    r = Repos(tmp_path, glossary=True)
    for root, term, push in ((r.wt, ADDED_OURS, False), (r.up, ADDED_THEIRS, True)):
        g = json.loads((root / SOURCE).read_text(encoding="utf-8"))
        g["terms"].append(term)
        write_json(root, SOURCE, g)
        assert glossary_run(root, "render")[0] == 0
        commit(root, "term")
        if push:
            git(root, "push", "-q", "origin", "develop")
    code, out, err = base_sync(r, "--push")
    assert code == 0, (out, err)
    assert results(out) == [("base", "glossary_merged"), ("push", "pushed")]
    assert not r.merging() and git(r.wt, "status", "--porcelain") == "" and r.remote_head() == r.head()
    terms = [t["term"] for t in json.loads((r.wt / SOURCE).read_text(encoding="utf-8"))["terms"]]
    assert ADDED_OURS["term"] in terms and ADDED_THEIRS["term"] in terms


def test_1917_3_other_conflict_goes_to_the_worker_and_is_finished_after(tmp_path):
    r = Repos(tmp_path, {"a.txt": "base\n"})
    r.work("a.txt", "ours\n")
    r.advance("a.txt", "theirs\n")
    code, out, err = base_sync(r, "--push")
    assert code == 1 and out["status"] == "stopped", (out, err)
    assert out["items"] == [{"kind": "conflict", "path": "a.txt", "result": "unresolved"}]
    assert r.merging() and "<<<<<<<" in (r.wt / "a.txt").read_text()  # worker は衝突の印のある worktree を受け取る
    code, out, _ = base_sync(r)  # 解く前に打ち直しても進まない
    assert code == 1 and r.merging()
    (r.wt / "a.txt").write_text("ours\ntheirs\n")  # worker が両方を残して解き、git add する
    git(r.wt, "add", "a.txt")
    code, out, err = base_sync(r, "--push")
    assert code == 0 and results(out) == [("base", "finished"), ("push", "pushed")], (out, err)
    assert not r.merging() and r.remote_head() == r.head()


def test_overlapping_uncommitted_change_is_refused_with_3(tmp_path):
    r = Repos(tmp_path, {"a.txt": "base\n"})
    r.advance("a.txt", "theirs\n")
    (r.wt / "a.txt").write_text("uncommitted\n")
    code, out, err = base_sync(r)
    assert code == 3 and out["status"] == "stopped" and not r.merging(), (out, err)
    assert (r.wt / "a.txt").read_text() == "uncommitted\n"


def test_unknown_base_is_unreadable_2(tmp_path):
    r = Repos(tmp_path)
    code, out, _ = merged(["base-sync", "--base", "no-such", "--root", str(r.wt)], r.wt)
    assert code == 2 and out["status"] == "stopped"


def test_pr_reads_the_base_from_the_pr(tmp_path):
    r = Repos(tmp_path)
    r.advance("theirs.txt", "theirs\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(f"#!{PY}\nimport json\nprint(json.dumps({{'baseRefName': 'develop', 'state': 'OPEN'}}))\n")
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    code, out, err = merged(["base-sync", "--pr", "5", "--push", "--root", str(r.wt)], r.wt, env)
    assert code == 0 and results(out) == [("base", "merged"), ("push", "pushed")], (out, err)
    assert out["metrics"]["base"] == "develop"


def test_2003_2_the_check_after_the_take_sees_the_combined_lines(tmp_path):
    """ベースブランチ側と作業ブランチ側が同じ文書の別の行へ 1 行ずつ足し、合成で初めて行数の上限（4 行）を超える。
    実装のプランの並び（base-sync → sync）を流すと、取り込んだ合成の上で sync が落ちる。"""
    r = Repos(tmp_path, {"doc.md": "a\nb\nc\n"})
    r.work("doc.md", "a\nb\nc\nmine\n")
    r.advance("doc.md", "theirs\na\nb\nc\n")
    check = f"{PY} -c \"import sys; sys.exit(len(open('doc.md').read().splitlines()) > 4)\""
    assert subprocess.run(check, shell=True, cwd=r.wt).returncode == 0  # ブランチ単独では通る
    *steps, _ = base_sync_steps("--base develop", "sync", push=False, on_error=None, returns=True)
    plan = {"フェーズ": "実装", "作業場所": str(r.wt), "steps": [*steps, {"id": "sync", "type": "run", "cmd": check, "next": "end"}]}
    text = engine.Engine(plan, tmp_path / "state").run(None)
    assert "結果: 止まった" in text and "ステップ sync が失敗した" in reason_of(text), text
    assert len((r.wt / "doc.md").read_text().splitlines()) == 5


# --- base-return ---------------------------------------------------------------


def state_with(tmp_path, returns: int, failed: int = 0) -> Path:
    log = [{"id": "merge", "exit": 4}] + [{"id": "base-return", "exit": 0}] * returns + [{"id": "base-return", "exit": 1}] * failed
    (tmp_path / "state.json").write_text(json.dumps({"log": log, "llm": []}))
    return tmp_path


@pytest.mark.parametrize("returns", [0, 1, 2])
def test_base_return_goes_back_up_to_the_limit(tmp_path, returns):
    code, out, _ = merged(["base-return", "--state", str(state_with(tmp_path, returns, failed=1))], tmp_path)
    assert code == 0 and out["metrics"] == {"returns": returns + 1, "limit": 3}


def test_1917_7_the_fourth_conflict_stops_with_the_count(tmp_path):
    code, out, _ = merged(["base-return", "--state", str(state_with(tmp_path, 3))], tmp_path)
    assert code == 1 and out["status"] == "stopped"
    assert "ベースブランチが進み続けて衝突した" in out["summary"] and "4 回目" in out["summary"]


def test_base_return_without_state_is_2(tmp_path):
    assert merged(["base-return", "--state", str(tmp_path / "none")], tmp_path)[0] == 2


# --- プランの形（#1917-1・5・6・8、#2003-1・3・4） ---------------------------------


def by_id(plan):
    return {s["id"]: s for s in plan["steps"]}


def test_every_template_builds_the_take_with_the_one_part(tpl):
    """I2・#2003-3: 取り込みのステップは base_sync_steps の返す形で、雛形にコマンドの文字列が残らない。"""
    for name in ("impl", "fix", "check", "sprint/5-impl-16", "sprint/6-check"):
        s = by_id(tpl[name])
        source = s["base-sync"]["cmd"].split(" base-sync ", 1)[1].split(" --push")[0].removesuffix(" --root .")
        want = base_sync_steps(source, s["base-sync"]["next"], push="--push" in s["base-sync"]["cmd"],
                               on_error=s["base-sync"].get("on_fail"), returns=True, stage=s["base-sync"].get("stage"))  # fmt: skip
        assert [s[w["id"]] for w in want] == want, name
        assert not [x for x in tpl[name]["steps"] if "git merge" in x.get("cmd", "")], name


def test_2003_1_impl_takes_the_base_after_the_work_and_before_the_checks(tpl):
    for name, first in (("impl", "impl"), ("sprint/5-impl-16", "impl"), ("fix", "base-sync")):
        plan = tpl[name]
        s = by_id(plan)
        assert plan["steps"][0]["id"] == first
        if first == "impl":
            assert s["impl"]["next"] == "base-sync"
        assert s["base-sync"]["next"] == "sync" and s["fix-base"]["next"] == "base-sync"  # #2003-4
        assert "--push" not in s["base-sync"]["cmd"] and "on_fail" not in s["base-sync"]  # 実装は止まる
        assert s["base-sync"]["cmd"].endswith("base-sync --base main --root .") or "sprint" in name


def test_1917_1_check_takes_the_base_before_the_whole_tests(tpl):
    for name in ("check", "sprint/6-check"):
        order = [x["id"] for x in tpl[name]["steps"]]
        s = by_id(tpl[name])
        assert s["review"]["next"] == "base-sync" and s["base-sync"]["next"] == "test-all", name
        assert order.index("base-sync") < order.index("test-all") < order.index("ready"), name
        assert "--pr " in s["base-sync"]["cmd"] and "--push" in s["base-sync"]["cmd"] and s["base-sync"]["on_fail"] == "abort"


@pytest.mark.parametrize("name", ["impl", "fix", "check", "sprint/5-impl-16", "sprint/6-check"])
def test_1917_5_6_merge_goes_back_to_the_take_on_conflict(tpl, name):
    s = by_id(tpl[name])
    assert s["merge"]["on_exit"]["4"] == s["merge-approved"]["on_exit"]["4"] == "base-return"
    assert s["base-return"]["next"] == "base-sync" and s["base-return"]["kind"] == limits.RETURN_KIND
    _, returns = limits.loops(tpl[name])
    assert "merge→base-return" in returns  # 前提 1: 戻りの輪が上限の計算に入る


def test_1917_8_callers_without_the_conflict_route_keep_their_routes(tpl):
    """スプリント PR・承認の後のマージ・promote など、取り込みのステップを持たないプランの merge は 4 の行き先を持たない。"""
    seen = 0
    for name, plan in tpl.items():
        if any(s["id"] == "base-sync" for s in plan["steps"]):
            continue
        for s in plan["steps"]:
            if "merge-when-green" in s.get("cmd", "") or " promote " in s.get("cmd", ""):
                seen += 1
                assert "4" not in (s.get("on_exit") or {}), (name, s["id"])
    assert seen
    from supervise_lib.verify_steps import merge_steps

    a = type("A", (), {"test_limits": {"ci_wait_timeout": 60}})()
    assert all(s.get("on_exit", {"75": "stop"}) == {"75": "stop"} for s in merge_steps(a, next="end"))


# --- 経路（スタブのエンジン） -------------------------------------------------------


IMPL_AFTER_TAKE = ("sync", "test-limited", *MATCH, "test-all", "doc-lint", "ready", "merge-gate")
CHECK_AFTER_TAKE = ("test-all", "ready", "merge-gate")


def test_impl_route_goes_back_three_times_and_merges(tpl, tmp_path):
    back = (("merge", 4), "base-return", "base-sync", *IMPL_AFTER_TAKE)
    route = _route("impl", "base-sync", *IMPL_AFTER_TAKE, *back, *back, *back, "merge")
    ran, text, _ = drive(tmp_path, tpl["sprint/5-impl-16"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 完了" in text, reason_of(text)


def test_check_route_goes_back_and_reaches_record(tpl, tmp_path):
    back = (("merge", 4), "base-return", "base-sync", *CHECK_AFTER_TAKE)
    head = ("collect", "pr", "assess", "refactor", "review", "base-sync")
    route = _route(*head, *CHECK_AFTER_TAKE, *back, *back, *back, "merge", "record")
    ran, text, _ = drive(tmp_path, tpl["sprint/6-check"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 完了" in text, reason_of(text)


def test_take_conflict_goes_to_the_worker_and_back(tpl, tmp_path):
    route = _route("impl", ("base-sync", 1), "fix-base", "base-sync", *IMPL_AFTER_TAKE, "merge")
    ran, text, _ = drive(tmp_path, tpl["sprint/5-impl-16"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 完了" in text, reason_of(text)


def test_check_stops_through_abort_when_returns_run_out(tpl, tmp_path):
    route = _route(
        *("collect", "pr", "assess", "refactor", "review", "base-sync"), *CHECK_AFTER_TAKE, ("merge", 4), ("base-return", 1), ("abort", 1)
    )
    ran, text, _ = drive(tmp_path, tpl["sprint/6-check"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 止まった" in text


# --- 止まった理由（決定 7） -----------------------------------------------------------


def said(summary, code):
    obj = {"tool": "t", "status": "stopped", "summary": summary, "items": [], "metrics": {}}
    return f"echo '{json.dumps(obj, ensure_ascii=False)}'; exit {code}"


@pytest.mark.parametrize("routed", [True, False])
def test_1917_7_the_stop_reason_carries_the_return_summary(tmp_path, routed):
    why = "ベースブランチが進み続けて衝突した（マージの直前の衝突 4 回目。戻りの上限 3 回）"
    back = {"id": "base-return", "type": "run", "cmd": said(why, 1), "next": "end"}
    steps = [{**back, "on_fail": "abort"}, {"id": "abort", "type": "run", "cmd": said("記録した", 1), "next": "end"}] if routed else [back]
    text = engine.Engine({"フェーズ": "検査", "作業場所": str(tmp_path), "steps": steps}, tmp_path / "state").run(None)
    reason = reason_of(text)
    assert why in reason and "4 回目" in reason, text
    if routed:
        assert "ステップ abort が失敗した" in reason and "base-return: " in reason
