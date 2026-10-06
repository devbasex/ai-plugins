"""release-steps.py: 承認したコミット（#815）。approval-facts・approve・release --channel prod の受け取りと比較。

release は一時の git リポジトリ（origin は bare）で動かし、GitHub（PR の作成・マージ・gh）だけを差し替える。
配布の PR のマージは、差し替えた wait_and_merge が別の clone で develop へ入れて push する。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release-steps.py"
VER = "1.2.3"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(root: Path, name: str, text: str, msg: str) -> str:
    (root / name).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def load(monkeypatch):
    spec = importlib.util.spec_from_file_location("release_steps_815", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_815", mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "emit", lambda obj, code=None, **k: (_ for _ in ()).throw(SystemExit((obj, code))))
    return mod


def clone(origin: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
    git(dest, "config", "user.email", "t@example.com")
    git(dest, "config", "user.name", "t")
    return dest


@pytest.fixture
def world(tmp_path: Path):
    """origin（bare）・作業場所（release/v1.2.3）・別の clone（ほかの人のマージ）と、承認したコミット。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "develop")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "t")
    (seed / ".ndf").mkdir()
    (seed / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "trunk", "production_branch": "live"}))
    (seed / ".ndf" / "supervise.json").write_text(json.dumps({"release": {"form": "package-plugin", "plugin": "foo"}}))
    commit(seed, "a.txt", "a\n", "init")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "HEAD:trunk", "HEAD:live")
    approved = commit(seed, "feature.txt", "f\n", "feature (#10)")
    git(seed, "push", "-q", "origin", "HEAD:trunk")
    work = clone(origin, tmp_path / "work")
    git(work, "checkout", "-q", "-b", f"release/v{VER}", "origin/trunk")
    commit(work, "version.txt", f"{VER}\n", f"bump {VER}")
    other = clone(origin, tmp_path / "other")
    git(other, "checkout", "-q", "trunk")
    return {"origin": origin, "work": work, "other": other, "approved": approved}


def run_release(monkeypatch, w, *, outside_after=False, move_head=False, **extra):
    """release --channel prod を流し、(結果, 終了コード, マージした PR と expect, gh の呼び出し) を返す。"""
    mod = load(monkeypatch)
    work, other = w["work"], w["other"]
    merged, ghcalls, state = [], [], {}
    monkeypatch.setattr(mod.bump, "require_bumped", lambda r, plugin, ver: None)
    monkeypatch.setattr(mod, "find_pr", lambda r, head, base, states: None)
    monkeypatch.setattr(mod, "run_checks", lambda r: [])
    monkeypatch.setattr(mod, "create_pr", lambda r, base, head, title, body: 1 if base == "trunk" else 2)

    def wait_and_merge(r, n, expect=None, ci_wait=3600.0):
        merged.append((n, expect))
        if n == 1:
            git(other, "fetch", "-q", "origin")
            git(other, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", f"origin/release/v{VER}")
            state["merge"] = git(other, "rev-parse", "HEAD")
            if outside_after:
                commit(other, "statusline.txt", "x\n", "statusline (#806)")
            git(other, "push", "-q", "origin", "HEAD:trunk")
            return state["merge"]
        if move_head:
            commit(other, "late.txt", "x\n", "late (#900)")
            git(other, "push", "-q", "origin", "HEAD:trunk")
            raise mod.HeadMoved("moved")
        git(other, "push", "-q", "origin", f"{expect}:live")
        return expect

    def pr_view(r, n, fields):
        head = git(work, "rev-parse", "HEAD")
        return {"headRefOid": head, "commits": [{"oid": head}], "mergeCommit": {"oid": state.get("merge")}}

    monkeypatch.setattr(mod, "wait_and_merge", wait_and_merge)
    monkeypatch.setattr(mod, "pr_view", pr_view)
    monkeypatch.setattr(mod, "changelog_section", lambda r, ver, plugin: "")
    monkeypatch.setattr(
        mod.gh_parts,
        "gh",
        lambda args, cwd=None: ghcalls.append(args) or subprocess.CompletedProcess(args, 0, '[{"number": 806}]', ""),
    )
    monkeypatch.setattr(mod.trash, "sweep", lambda r, m: ([], {}))
    ns = {
        "root": str(work),
        "version": VER,
        "plugins": None,
        "channel": "prod",
        "approved_sha": None,
        "approval": None,
        "ci_wait": 3600.0,
        **extra,
    }
    with pytest.raises(SystemExit) as e:
        mod.cmd_release(argparse.Namespace(**ns))
    out, code = e.value.code
    return out, code, merged, ghcalls


def tags(origin: Path) -> str:
    return git(origin, "tag")


def test_release_after_only_the_release_pr_is_ok(monkeypatch, world):
    """受け入れ条件 3: 承認の後が配布の PR だけなら、比べた先端を本番チャネルへ入れてタグと Release を作る。"""
    out, code, merged, ghcalls = run_release(monkeypatch, world, approved_sha=world["approved"])
    tip = git(world["origin"], "rev-parse", "trunk")
    assert (out["status"], code) == ("ok", None)
    assert out["metrics"]["approved_sha"] == world["approved"] and out["metrics"]["compared_head"] == tip
    assert merged == [(1, None), (2, tip)]  # 比べた先端だけをマージさせる（受け入れ条件 6）
    assert tags(world["origin"]) == f"foo--v{VER}"
    assert any(a[:2] == ["release", "create"] for a in ghcalls)


def test_release_with_an_outside_pr_after_approval_stops_at_the_gate(monkeypatch, world):
    """受け入れ条件 5: 配布の PR の外の変更があれば、本番チャネルの PR・タグ・Release を作らずに 10。"""
    out, code, merged, ghcalls = run_release(monkeypatch, world, outside_after=True, approved_sha=world["approved"])
    assert (out["status"], code) == ("gate", 10)
    assert merged == [(1, None)] and tags(world["origin"]) == ""
    assert not any(a[:2] == ["release", "create"] for a in ghcalls)
    bad = [i for i in out["items"] if i.get("result") == "unapproved"]
    assert [(i["subject"], i["pr"]) for i in bad] == [("statusline (#806)", 806)] and len(bad[0]["name"]) == 40
    m = out["metrics"]
    assert (m["approved_sha"], m["reason"], m["outside"], m["main_pr"], m["tag"]) == (world["approved"], "outside_commits", 1, None, None)
    assert m["compared_head"] == git(world["origin"], "rev-parse", "trunk")
    assert "approval-facts" in out["next"] and "--approved-sha" in out["next"]


def test_release_stops_when_the_head_moves_while_waiting(monkeypatch, world):
    """受け入れ条件 6: 比べた後に先端が進めば、本番チャネルへマージせず 10。"""
    out, code, merged, _ = run_release(monkeypatch, world, move_head=True, approved_sha=world["approved"])
    assert (out["status"], code) == ("gate", 10)
    assert git(world["origin"], "rev-parse", "live") != git(world["origin"], "rev-parse", "trunk")
    assert tags(world["origin"]) == "" and out["metrics"]["reason"] == "outside_commits"


def test_release_with_a_commit_that_is_not_an_ancestor_stops_at_the_gate(monkeypatch, world):
    """受け入れ条件 7: 先端の祖先でない・無い SHA は 10。"""
    out, code, merged, _ = run_release(monkeypatch, world, approved_sha="f" * 40)
    assert (out["status"], code, out["metrics"]["reason"]) == ("gate", 10, "unknown_commit")
    assert merged == [(1, None)] and tags(world["origin"]) == ""
    assert {"kind": "commit", "name": "f" * 40, "result": "unknown"} in out["items"]


@pytest.mark.parametrize(
    "extra, code, text",
    [
        ({}, 2, "--approved-sha"),
        ({"approved_sha": "abc"}, 2, "40 桁"),
        ({"approval": "none.md"}, 3, "無い"),
    ],
)
def test_release_without_an_approved_commit_merges_nothing(monkeypatch, world, extra, code, text):
    """受け入れ条件 4・I1: 承認したコミットが無い・形が違うと、push も PR のマージもせずに 2 / 3。"""
    mod = load(monkeypatch)
    calls = []
    monkeypatch.setattr(mod, "git", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("git")))
    ns = {
        "root": str(world["work"]),
        "version": VER,
        "plugins": None,
        "channel": "prod",
        "approved_sha": None,
        "approval": None,
        "ci_wait": 3600.0,
    }
    with pytest.raises(mod.StepError) as e:
        mod.cmd_release(argparse.Namespace(**{**ns, **extra}))
    assert e.value.code == code and text in str(e.value) and not calls


def test_release_dev_rejects_an_approved_commit(monkeypatch, world):
    """受け入れ条件 9: dev には承認したコミットを渡さない。"""
    mod = load(monkeypatch)
    ns = argparse.Namespace(
        root=str(world["work"]), version=VER, plugins=None, channel="dev", approved_sha="a" * 40, approval=None, ci_wait=3600.0
    )
    with pytest.raises(mod.StepError) as e:
        mod.cmd_release(ns)
    assert e.value.code == 2


MATERIAL = "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 承認したコミット | {sha} |\n| 版数 | 1.2.3 |\n"


def test_approve_then_release_with_the_material(monkeypatch, world, tmp_path):
    """I10: --approval は承認の記録があり SHA が同じときだけ受け取る。記録が無ければ何もマージせずに 10。"""
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(sha=world["approved"]))
    out, code, merged, _ = run_release(monkeypatch, world, approval=str(path))
    assert (out["status"], code, merged) == ("gate", 10, [])
    assert out["items"] == [{"kind": "commit", "name": world["approved"], "result": "not_approved", "recorded": None}]

    mod = load(monkeypatch)
    with pytest.raises(mod.StepError) as e:  # 提示した SHA と資料が違えば書かない
        mod.cmd_approve(argparse.Namespace(root=str(world["work"]), approval=str(path), approved_sha="a" * 40, by="user"))
    assert e.value.code == 1
    with pytest.raises(SystemExit) as ok:
        mod.cmd_approve(argparse.Namespace(root=str(world["work"]), approval=str(path), approved_sha=world["approved"], by="user"))
    assert ok.value.code[0]["status"] == "ok"
    out, code, merged, _ = run_release(monkeypatch, world, approval=str(path))
    assert (out["status"], out["metrics"]["approved_sha"]) == ("ok", world["approved"])


def test_approval_facts_writes_the_approved_commit(monkeypatch, world, tmp_path):
    """受け入れ条件 1・2・I8: metrics.approved_sha・承認資料の欄・比較の URL・next が同じ 40 桁を持つ。"""
    mod = load(monkeypatch)
    work = world["work"]
    git(work, "tag", "foo--v1.0.0", "origin/live")
    git(work, "push", "-q", "origin", "foo--v1.0.0")
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(tmp_path / "present"))
    monkeypatch.setattr(mod.repo_lib, "owner_repo", lambda r: "o/n")
    monkeypatch.setattr(mod, "pr_view", lambda r, n, f: {"number": n, "title": "t", "state": "MERGED", "mergeCommit": {"oid": "c" * 40}})
    monkeypatch.setattr(mod, "pr_check_buckets", lambda r, n: [])
    with pytest.raises(SystemExit) as e:
        mod.cmd_approval_facts(argparse.Namespace(root=str(work), version=VER, prs=[10], prev_tag=None, plugin=None))
    out, code = e.value.code
    sha = world["approved"]
    assert code == 10 and out["metrics"]["approved_sha"] == sha
    assert out["metrics"]["compare"].endswith(f"...{sha}") and f"--approved-sha {sha}" in out["next"]
    assert mod.ac.material_sha(out["presentation_path"]) == sha
    assert mod.ac.recorded_sha(out["presentation_path"]) is None
