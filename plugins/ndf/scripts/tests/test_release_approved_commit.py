"""release-steps.py: 承認したコミット（#815）。approval-facts・approve・release --channel prod の受け取りと比較。

release は一時の git リポジトリ（origin は bare）で動かし、GitHub（PR の作成・マージ・gh）だけを差し替える。
デプロイの PR のマージは、差し替えた wait_and_merge が別の clone で develop へ入れて push する。
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
    (root / name).parent.mkdir(parents=True, exist_ok=True)
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
    """origin（bare）・作業場所（承認したコミットから切った release/v1.2.3）・別の clone（ほかの人のマージ）と、承認したコミット。"""
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
    other = clone(origin, tmp_path / "other")
    git(other, "checkout", "-q", "trunk")
    w = {"origin": origin, "work": work, "other": other, "approved": approved}
    cut_release(w)
    return w


def cut_release(w):
    """作業場所に release/v1.2.3 を origin/trunk の先頭から切り（切り直し）、版上げを積む。先頭を返す。"""
    work = w["work"]
    git(work, "fetch", "-q", "origin")
    git(work, "checkout", "-q", "-B", f"release/v{VER}", "origin/trunk")
    return commit(work, "version.txt", f"{VER}\n", f"bump {VER}")


SHIPPED_FILE = "plugins/foo/statusline.txt"
DOC_FILE = "docs/design.md"
SYNC = f"release/v{VER}-sync"


def run_release(
    monkeypatch, w, *, outside_after=None, move_head=None, dev_bump=None, prod_merged=False, published=(), delete_on_merge=False, **extra
):
    """release --channel prod を流し、(結果, 終了コード, マージした PR と expect, gh の呼び出し) を返す。
    outside_after はリリースの PR の後に起点のブランチへ、move_head は本番チャネルへのマージを待つ間に（1 回だけ）
    release/v<版> へ別の人が入れるファイルのパス。dev_bump は承認の後に起点のブランチへ入る version.txt の中身
    （リリースの PR と衝突する）。prod_merged は本番の PR がマージ済みの再開、published はすでにある tag / release。
    PR の番号は 1 = release/v<版> → trunk、2 = release/v<版> → live、3 = release/v<版>-sync → trunk。"""
    mod = load(monkeypatch)
    work, other = w["work"], w["other"]
    if dev_bump:
        commit(other, "version.txt", dev_bump, "dev bump (#700)")
        git(other, "push", "-q", "origin", "HEAD:trunk")
    merged, ghcalls, created, state = [], [], [], {}
    monkeypatch.setattr(mod.bump, "require_bumped", lambda r, plugin, ver: None)
    if prod_merged:
        state["live"] = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:release/v{VER}", "HEAD:live")
        git(other, "fetch", "-q", "origin")
        git(other, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", f"origin/release/v{VER}")
        git(other, "push", "-q", "origin", "HEAD:trunk")
        for name in published:
            if name == "tag":
                git(work, "tag", "-a", f"foo--v{VER}", state["live"], "-m", "t")
                git(work, "push", "-q", "origin", f"foo--v{VER}")
                git(work, "tag", "-d", f"foo--v{VER}")

    def find_pr(r, head, base, states):
        if prod_merged and head == f"release/v{VER}":
            return {"number": 1 if base == "trunk" else 2, "state": "MERGED"}
        return None

    def create_pr(r, base, head, title, body):
        created.append((base, head))
        return {("trunk", f"release/v{VER}"): 1, ("live", f"release/v{VER}"): 2, ("trunk", SYNC): 3}[(base, head)]

    def wait_and_merge(r, n, expect=None, ci_wait=3600.0, gate_approved=None):
        merged.append((n, expect))
        if n in (1, 3):
            git(other, "fetch", "-q", "origin")
            git(other, "reset", "-q", "--hard", "origin/trunk")
            src = f"origin/release/v{VER}" if n == 1 else f"origin/{SYNC}"
            if subprocess.run(["git", "-C", str(other), "merge", "-q", "--no-ff", "-m", f"Merge pull request #{n}", src]).returncode:
                git(other, "merge", "--abort")
                raise mod.Conflicted(f"PR #{n} がベースブランチと衝突している")
            state["merge"] = git(other, "rev-parse", "HEAD")
            if outside_after:
                commit(other, outside_after, "x\n", "statusline (#806)")
            git(other, "push", "-q", "origin", "HEAD:trunk")
            if delete_on_merge:  # delete_branch_on_merge: GitHub がマージした head のブランチを消す
                git(w["origin"], "update-ref", "-d", f"refs/heads/{src.removeprefix('origin/')}")
            return state["merge"]
        if move_head and not state.get("moved"):
            state["moved"] = True
            git(other, "fetch", "-q", "origin")
            git(other, "checkout", "-q", "-B", "rel", f"origin/release/v{VER}")
            commit(other, move_head, "x\n", "late (#900)")
            git(other, "push", "-q", "origin", f"HEAD:release/v{VER}")
            git(other, "checkout", "-q", "trunk")
            raise mod.HeadMoved("moved")
        assert expect == git(w["origin"], "rev-parse", f"release/v{VER}")  # 比べた release/v<版> の先頭だけを出す
        git(w["origin"], "update-ref", "refs/heads/live", expect)
        state["live"] = expect
        return expect

    def pr_view(r, n, fields):
        head = git(work, "rev-parse", "HEAD")
        return {"headRefOid": head, "commits": [{"oid": head}], "mergeCommit": {"oid": state.get("live" if n == 2 else "merge")}}

    def gh(args, cwd=None):
        ghcalls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, 0 if "release" in published else 1, "", "")
        return subprocess.CompletedProcess(args, 0, '[{"number": 806}]', "")

    monkeypatch.setattr(mod, "find_pr", find_pr)
    monkeypatch.setattr(mod, "run_checks", lambda r: [])
    monkeypatch.setattr(mod, "create_pr", create_pr)
    monkeypatch.setattr(mod, "wait_and_merge", wait_and_merge)
    monkeypatch.setattr(mod, "pr_view", pr_view)
    monkeypatch.setattr(mod, "changelog_section", lambda r, ver, plugin: "")
    monkeypatch.setattr(mod.gh_parts, "gh", gh)
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
    return out, code, merged, ghcalls, created


def tags(origin: Path) -> str:
    return git(origin, "tag")


def shipped(w, path: str) -> bool:
    return subprocess.run(["git", "-C", str(w["origin"]), "cat-file", "-e", f"live:{path}"]).returncode == 0


def test_release_after_only_the_release_pr_is_ok(monkeypatch, world):
    """受け入れ条件 3・#2436 の R2: 承認したコミットと版上げだけの release/v<版> を head に本番チャネルへ入れ、タグと Release を作る。"""
    out, code, merged, ghcalls, created = run_release(monkeypatch, world, approved_sha=world["approved"])
    tip = git(world["origin"], "rev-parse", f"release/v{VER}")
    assert (out["status"], code) == ("ok", None)
    assert out["metrics"]["approved_sha"] == world["approved"] and out["metrics"]["compared_head"] == tip
    assert merged == [(1, None), (2, tip)]  # 比べた先端だけをマージさせる（受け入れ条件 6）
    assert created == [("trunk", f"release/v{VER}"), ("live", f"release/v{VER}")]  # 本番の PR の head は release/v<版>
    assert git(world["origin"], "merge-base", "--is-ancestor", tip, "trunk") == ""  # R3・R4: 先に起点へ取り込んである
    assert tags(world["origin"]) == f"foo--v{VER}"
    assert any(a[:2] == ["release", "create"] for a in ghcalls)


def test_release_restores_the_release_branch_deleted_by_the_merge(monkeypatch, world):
    """開発版の PR のマージで origin の release/v<版> が消えても（delete_branch_on_merge）、push し直して本番へ出す。"""
    out, code, merged, _, created = run_release(monkeypatch, world, approved_sha=world["approved"], delete_on_merge=True)
    tip = git(world["origin"], "rev-parse", f"release/v{VER}")
    assert (out["status"], code) == ("ok", None)
    assert merged == [(1, None), (2, tip)] and created[-1] == ("live", f"release/v{VER}")


def test_release_after_the_base_moved_ships_only_the_approved_commit(monkeypatch, world):
    """#2436 の R2: 承認の後に起点のブランチが進んでも止まらず、進んだ分は本番チャネルへ入れない（次のリリース）。"""
    out, code, merged, _, _ = run_release(monkeypatch, world, outside_after=SHIPPED_FILE, approved_sha=world["approved"])
    assert (out["status"], code) == ("ok", None)
    assert not shipped(world, SHIPPED_FILE) and shipped(world, "version.txt")
    assert tags(world["origin"]) == f"foo--v{VER}"


def test_release_cut_after_the_approved_commit_stops_at_the_gate(monkeypatch, world):
    """#2436 の R2: release/v<版> に承認の外のコミットがあれば、本番チャネルの PR・タグ・Release を作らずに 10。"""
    late = commit(world["other"], SHIPPED_FILE, "x\n", "statusline (#806)")
    git(world["other"], "push", "-q", "origin", "HEAD:trunk")
    cut_release(world)
    out, code, merged, ghcalls, created = run_release(monkeypatch, world, approved_sha=world["approved"])
    assert (out["status"], code) == ("gate", 10)
    assert merged == [(1, None)] and tags(world["origin"]) == "" and len(created) == 1
    assert not any(a[:2] == ["release", "create"] for a in ghcalls)
    bad = [i for i in out["items"] if i.get("result") == "unapproved"]
    assert [(i["name"], i["subject"], i["pr"]) for i in bad] == [(late, "statusline (#806)", 806)]
    m = out["metrics"]
    assert (m["approved_sha"], m["reason"], m["outside"], m["main_pr"], m["tag"]) == (world["approved"], "outside_commits", 1, None, None)
    assert m["compared_head"] == git(world["origin"], "rev-parse", f"release/v{VER}")
    assert "approval-facts" in out["next"] and "--approved-sha" in out["next"]


def test_release_stops_when_the_head_moves_while_waiting(monkeypatch, world):
    """受け入れ条件 6: 比べた後に release/v<版> の先頭が進めば、本番チャネルへマージせず 10。"""
    out, code, merged, _, _ = run_release(monkeypatch, world, move_head=SHIPPED_FILE, approved_sha=world["approved"])
    assert (out["status"], code) == ("gate", 10)
    assert git(world["origin"], "rev-parse", "live") != git(world["origin"], "rev-parse", f"release/v{VER}")
    assert tags(world["origin"]) == "" and out["metrics"]["reason"] == "outside_commits"


def test_release_retries_when_the_head_moves_by_a_change_that_ships_nothing(monkeypatch, world):
    """#2221: 待つ間にリリース対象に触れないコミットで先端が進めば、比べ直した先端で入れ直し、通したコミットを残す。"""
    out, code, merged, _, _ = run_release(monkeypatch, world, move_head=DOC_FILE, approved_sha=world["approved"])
    tip = git(world["origin"], "rev-parse", f"release/v{VER}")
    assert (out["status"], code) == ("ok", None)
    assert merged[-1] == (2, tip) and len(merged) == 3
    assert git(world["origin"], "rev-parse", "live") == tip and out["metrics"]["compared_head"] == tip
    assert [i["subject"] for i in out["items"] if i.get("result") == "outside_unshipped"] == ["late (#900)"]


def test_release_with_a_commit_that_is_not_an_ancestor_stops_at_the_gate(monkeypatch, world):
    """受け入れ条件 7: 先端の祖先でない・無い SHA は 10。"""
    out, code, merged, _, _ = run_release(monkeypatch, world, approved_sha="f" * 40)
    assert (out["status"], code, out["metrics"]["reason"]) == ("gate", 10, "unknown_commit")
    assert merged == [(1, None)] and tags(world["origin"]) == ""
    assert {"kind": "commit", "name": "f" * 40, "result": "unknown"} in out["items"]


def test_release_with_a_version_conflict_merges_through_the_sync_branch(monkeypatch, world):
    """#2436 の版数の衝突: 起点の版数が上がっていれば release/v<版>-sync で本番の版を取って取り込み、
    照合には元の release/v<版> の PR を使い、本番の PR の head は release/v<版> のまま。"""
    out, code, merged, _, created = run_release(monkeypatch, world, dev_bump="1.2.4-dev.1\n", approved_sha=world["approved"])
    tip = git(world["origin"], "rev-parse", f"release/v{VER}")
    assert (out["status"], code) == ("ok", None)
    assert merged == [(1, None), (3, None), (2, tip)]
    assert created == [("trunk", f"release/v{VER}"), ("trunk", SYNC), ("live", f"release/v{VER}")]
    assert (out["metrics"]["release_pr"], out["metrics"]["sync_pr"]) == (1, 3)
    assert git(world["origin"], "merge-base", "--is-ancestor", tip, "trunk") == ""  # R1: release/v<版> の先頭は起点の祖先
    assert git(world["origin"], "show", "trunk:version.txt") == VER  # 本番の版を取った
    assert git(world["origin"], "rev-parse", f"{SYNC}^1") == tip  # release/v<版> そのものへは起点を取り込まない
    assert git(world["origin"], "show", "live:version.txt") == VER


def test_release_with_a_conflict_outside_the_versions_stops(monkeypatch, world):
    """#2436 の R5: 版数の外が衝突すれば解かずに 1 で止め、本番の PR を作らない。"""
    mod_err = None
    try:
        run_release(monkeypatch, world, dev_bump="something else\n", approved_sha=world["approved"])
    except Exception as e:  # noqa: BLE001
        mod_err = e
    assert mod_err is not None and getattr(mod_err, "code", None) == 1 and "version.txt" in str(mod_err)
    assert git(world["origin"], "rev-parse", "live") != git(world["origin"], "rev-parse", f"release/v{VER}")
    assert tags(world["origin"]) == ""


@pytest.mark.parametrize("published", [(), ("tag",), ("tag", "release")])
def test_release_after_the_production_pr_merged_makes_only_what_is_missing(monkeypatch, world, published):
    """#2436 の再開: 本番の PR がマージ済みなら照合とマージを飛ばし、タグと GitHub Release のうち無いものだけを作る。"""
    out, code, merged, ghcalls, created = run_release(
        monkeypatch, world, prod_merged=True, published=published, approved_sha=world["approved"]
    )
    assert (out["status"], code) == ("ok", None)
    assert merged == [] and created == []
    assert tags(world["origin"]) == f"foo--v{VER}"
    assert git(world["origin"], "rev-parse", f"foo--v{VER}^{{commit}}") == git(world["origin"], "rev-parse", "live")
    assert any(a[:2] == ["release", "create"] for a in ghcalls) == ("release" not in published)
    assert out["metrics"]["main_pr"] == 2


def test_release_resume_stops_when_the_tag_points_elsewhere(monkeypatch, world):
    """再開でも、origin にあるタグが本番チャネルのコミットを指していなければ作ったものとみなさず止める。"""
    git(world["work"], "tag", "-a", f"foo--v{VER}", world["approved"], "-m", "t")
    git(world["work"], "push", "-q", "origin", f"foo--v{VER}")
    git(world["work"], "tag", "-d", f"foo--v{VER}")
    with pytest.raises(Exception) as e:
        run_release(monkeypatch, world, prod_merged=True, approved_sha=world["approved"])
    assert getattr(e.value, "code", None) == 1 and f"foo--v{VER}" in str(e.value)


def test_release_resume_stops_when_the_merged_pr_lacks_the_approved_commit(monkeypatch, world):
    """再開でも、マージ済みの本番の PR が承認したコミットを含まなければ続けない。"""
    side = git(world["work"], "commit-tree", "HEAD^{tree}", "-m", "side")  # 本番チャネルに無いコミット
    with pytest.raises(Exception) as e:
        run_release(monkeypatch, world, prod_merged=True, approved_sha=side)
    assert getattr(e.value, "code", None) == 1 and side[:8] in str(e.value)


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
    out, code, merged, _, _ = run_release(monkeypatch, world, approval=str(path))
    assert (out["status"], code, merged) == ("gate", 10, [])
    assert out["items"] == [{"kind": "commit", "name": world["approved"], "result": "not_approved", "recorded": None}]

    mod = load(monkeypatch)
    with pytest.raises(mod.StepError) as e:  # 提示した SHA と資料が違えば書かない
        mod.cmd_approve(argparse.Namespace(root=str(world["work"]), approval=str(path), approved_sha="a" * 40, by="user"))
    assert e.value.code == 1
    with pytest.raises(SystemExit) as ok:
        mod.cmd_approve(argparse.Namespace(root=str(world["work"]), approval=str(path), approved_sha=world["approved"], by="user"))
    assert ok.value.code[0]["status"] == "ok"
    out, code, merged, _, _ = run_release(monkeypatch, world, approval=str(path))
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
    monkeypatch.setattr(mod.included.merged_range.gh_rest, "pr_head_branches", lambda ns, cwd=None: ({10: "feat/x"}, ""))
    with pytest.raises(SystemExit) as e:
        mod.cmd_approval_facts(argparse.Namespace(root=str(work), version=VER, prs=[10], prev_tag=None, plugin=None))
    out, code = e.value.code
    sha = world["approved"]
    assert code == 10 and out["metrics"]["approved_sha"] == sha
    assert out["metrics"]["compare"].endswith(f"...{sha}") and f"--approved-sha {sha}" in out["next"]
    assert mod.ac.material_sha(out["presentation_path"]) == sha
    assert mod.ac.recorded_sha(out["presentation_path"]) is None


def facts_dir(tmp_path: Path, sha: str | None) -> Path:
    """開発版のプランの状態ディレクトリ（facts のステップの出力だけ）。"""
    d = tmp_path / "dev-state"
    d.mkdir(exist_ok=True)
    line = json.dumps({"tool": "release", "status": "gate", "summary": "s", "items": [], "metrics": {"approved_sha": sha} if sha else {}})
    (d / "12-facts.out").write_text("進捗\n" + line + "\n")
    return d


def approve_facts(mod, world, path, facts, answer=None):
    ns = argparse.Namespace(
        root=str(world["work"]), approval=str(path), approved_sha=None, facts_from=str(facts), answer_from=answer, by="user"
    )
    with pytest.raises(SystemExit) as out:
        mod.cmd_approve(ns)
    return out.value.code


def answer_state(tmp_path: Path, at: str) -> str:
    state = tmp_path / "sprint.json"
    state.write_text(json.dumps({"gates": [{"name": "リリース承認ゲート", "what": "本番", "at": at, "by": "user", "outcome": "approved"}]}))
    return str(state)


def test_approve_from_facts_needs_the_users_answer_after_facts(monkeypatch, world, tmp_path):
    """#1426 の AC10〜12: facts の SHA と資料が違う・利用者の承認の記録が無いか facts より前なら、書かずに 10。
    facts より後の記録があれば書き、release が承認の照合を通る。"""
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(sha=world["approved"]))
    mod = load(monkeypatch)
    out, code = approve_facts(mod, world, path, facts_dir(tmp_path, "a" * 40), answer_state(tmp_path, "2999-01-01T00:00:00Z"))
    assert (out["status"], code) == ("gate", 10)
    assert "違う" in out["summary"]
    facts = facts_dir(tmp_path, world["approved"])
    for answer in (None, answer_state(tmp_path, "2000-01-01T00:00:00Z")):
        out, code = approve_facts(mod, world, path, facts, answer)
        assert (out["status"], code) == ("gate", 10)
    out, code, merged, _, _ = run_release(monkeypatch, world, approval=str(path))
    assert (out["status"], merged) == ("gate", [])  # release へ進んでも承認の記録が無いため止まる
    mod = load(monkeypatch)
    out, code = approve_facts(mod, world, path, facts, answer_state(tmp_path, "2999-01-01T00:00:00Z"))
    assert (out["status"], out["items"][0]["result"]) == ("ok", "written")
    # 記録が既にあれば、証跡を渡さなくても書かずに通す（--state の無いスプリントで conductor が approve を打った後）
    out, code = approve_facts(mod, world, path, facts)
    assert (out["status"], out["items"][0]["result"]) == ("ok", "kept")
    out, code, merged, _, _ = run_release(monkeypatch, world, approval=str(path))
    assert (out["status"], out["metrics"]["approved_sha"]) == ("ok", world["approved"])


def test_approve_from_facts_reads_sha_and_time_from_the_newest_output(monkeypatch, world, tmp_path):
    """facts を再開すると NN が 01 に戻る（#2247）。古い回の 12 の SHA と新しい回の 01 の時刻を組み合わせず、
    最後に書かれた 01 の SHA と時刻を使う。"""
    import os

    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(sha=world["approved"]))
    d = facts_dir(tmp_path, "a" * 40)  # 古い回の 12-facts.out（古い SHA）
    os.utime(d / "12-facts.out", (1_000_000_000, 1_000_000_000))
    line = json.dumps({"tool": "release", "status": "gate", "summary": "s", "items": [], "metrics": {"approved_sha": world["approved"]}})
    (d / "01-facts.out").write_text(line + "\n")
    mod = load(monkeypatch)
    out, code = approve_facts(mod, world, path, d, answer_state(tmp_path, "2999-01-01T00:00:00Z"))
    assert (out["status"], out["items"][0]["result"]) == ("ok", "written")
    assert out["metrics"]["approved_sha"] == world["approved"]


def test_approve_from_facts_without_facts_output_stops_at_the_gate(monkeypatch, world, tmp_path):
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(sha=world["approved"]))
    mod = load(monkeypatch)
    out, code = approve_facts(mod, world, path, facts_dir(tmp_path, None), answer_state(tmp_path, "2999-01-01T00:00:00Z"))
    assert (out["status"], code) == ("gate", 10)
    with pytest.raises(mod.StepError) as e:
        mod.cmd_approve(
            argparse.Namespace(root=str(world["work"]), approval=str(path), approved_sha="a" * 40, facts_from=str(tmp_path), by="user")
        )
    assert e.value.code == 2


@pytest.mark.parametrize("facts", ["", "{queue_state?:release}"])
def test_approve_without_readable_facts_passes_only_with_an_existing_record(monkeypatch, world, tmp_path, facts):
    """--dev-state を省いた単発の本番のプラン: queue は facts の置き場を空で、run は置き換えずに渡す（#2247）。
    記録が無ければ 10（2 の引数の誤りにしない）、資料に一致する記録が既にあれば書かずに通す（決定 7）。"""
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(sha=world["approved"]))
    monkeypatch.chdir(facts_dir(tmp_path, world["approved"]))  # 空の置き場が作業場所（.）の facts を拾わない
    mod = load(monkeypatch)
    out, code = approve_facts(mod, world, path, facts)
    assert (out["status"], code) == ("gate", 10) and "--dev-state" in out["next"]
    mod.ac.record_approval(path, world["approved"], "user", "2026-10-31T00:00:00Z")
    out, code = approve_facts(mod, world, path, facts)
    assert (out["status"], out["items"][0]["result"]) == ("ok", "kept")
    assert out["metrics"]["approved_sha"] == world["approved"]
