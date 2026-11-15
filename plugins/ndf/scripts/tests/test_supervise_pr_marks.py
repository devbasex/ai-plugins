"""supervise_lib/pr_marks.py と pr のステップ: 本文を組み直しても ndf-next の検査（I2・port）の印の行を保ち、
印が要るのに無ければ pr の時点で止める（#3529）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import gh_call  # noqa: E402
from supervise_lib import pr_marks  # noqa: E402
from supervise_lib.pr import PR_FOOTER, PrStep  # noqa: E402

CONF = {
    "old_plugin": "plugins/ndf",
    "immediate_fix_mark": "即時修正（plugins/ndf）:",
    "port_mark": "移す先（ndf-next）:",
    "no_port_mark": "移さない理由（ndf-next）:",
}
FIX = "即時修正（plugins/ndf）: 停止の不具合を直す"
NO_PORT = "移さない理由（ndf-next）: 旧いプラグインだけの直し"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def commit(root, rel, text="x\n"):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", f"add {rel}")


@pytest.fixture
def repo(tmp_path):
    """rules.json を持ち、origin の develop から切った feat/x の作業場所。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    commit(root, str(pr_marks.RULES), json.dumps({"invariants": CONF}, ensure_ascii=False))
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "origin", "develop")
    git(root, "fetch", "-q", "origin")
    git(root, "checkout", "-q", "-b", "feat/x")
    return root


def test_conf_is_none_without_the_rules(tmp_path):
    assert pr_marks.marks_conf(tmp_path) is None


def test_carry_keeps_the_mark_lines_of_the_current_body_before_the_footer():
    old = f"古い本文\n\n{FIX}\n{NO_PORT}\n\n{PR_FOOTER}\n"
    new = f"## 利用者向けの変化\n\n- 直した\n\n{PR_FOOTER}\n"
    out = pr_marks.carry_marks(new, old, CONF, PR_FOOTER)
    assert FIX in out and NO_PORT in out and out.rstrip().endswith(PR_FOOTER)
    assert pr_marks.carry_marks(out, old, CONF, PR_FOOTER) == out  # 2 回目は行を増やさない


@pytest.mark.parametrize(
    "files, body, want",
    [
        (["plugins/ndf-next/a.py"], "", []),
        (["plugins/ndf/a.py"], NO_PORT, []),
        (["plugins/ndf/a.py"], "移す先（ndf-next）: #12", []),
        (["plugins/ndf/a.py"], "移す先（ndf-next）: あとで", ["port"]),
        (["plugins/ndf/a.py"], "", ["port"]),
        (["plugins/ndf/a.py", "plugins/ndf-next/a.py"], NO_PORT, ["I2"]),
        (["plugins/ndf/a.py", "plugins/ndf-next/a.py"], f"{FIX}\n{NO_PORT}", []),
    ],
)
def test_missing_follows_the_rules_of_the_check(files, body, want):
    assert [k for k, _ in pr_marks.missing_marks(body, CONF, files)] == want


def run_step(monkeypatch, repo, old_body):
    """既存の PR（本文 old_body）へ pr のステップを機械の本文で流し、(結果, 送った本文) を返す。"""
    sent = {}

    def fake(args, stdin=None, cwd=None):
        if args[:2] == ["pr", "list"] and "url,baseRefName" in args:
            return gh_call.GhResult(0, "https://github.com/o/r/pull/5\tdevelop\n", "")
        if args[:2] == ["pr", "list"]:
            return gh_call.GhResult(0, old_body, "")
        if args[:2] == ["pr", "edit"]:
            sent["body"] = args[args.index("--body") + 1]
        return gh_call.GhResult(0, "", "")

    monkeypatch.setattr(gh_call, "gh", fake)
    monkeypatch.setattr(PrStep, "_push", lambda self, ctx: ("feat/x", None))
    ctx = SimpleNamespace(
        cwd=str(repo),
        plan={"課題": [3529]},
        steps={},
        state=SimpleNamespace(cur={}, results={}, log=[], dir=repo),
        base_branch=lambda: "develop",
    )
    ok, text = PrStep().execute(ctx, {"id": "pr", "body": "machine", "title": "題"})
    return ok, text, sent.get("body", ""), ctx.state.cur


def test_rerunning_the_pr_step_keeps_the_marks(monkeypatch, repo):
    commit(repo, "plugins/ndf/a.py")
    commit(repo, "plugins/ndf-next/a.py")
    ok, _, body, _ = run_step(monkeypatch, repo, f"前の本文\n\n{FIX}\n\n{NO_PORT}\n\n{PR_FOOTER}\n")
    assert ok and FIX in body and NO_PORT in body


def test_the_pr_step_stops_when_the_marks_are_missing(monkeypatch, repo):
    commit(repo, "plugins/ndf/a.py")
    commit(repo, "plugins/ndf-next/a.py")
    ok, text, body, cur = run_step(monkeypatch, repo, f"前の本文\n\n{PR_FOOTER}\n")
    assert not ok and body  # PR は出してから止める（本文へ印を足して --from で再開できる）
    assert cur["exit"] == 1 and CONF["immediate_fix_mark"] in text and CONF["no_port_mark"] in text


def test_the_pr_step_does_not_stop_for_new_plugin_only_diffs(monkeypatch, repo):
    commit(repo, "plugins/ndf-next/a.py")
    ok, _, _, _ = run_step(monkeypatch, repo, "")
    assert ok


def test_the_pr_step_does_not_judge_prs_to_the_default_branch(monkeypatch, repo):
    """CI は既定ブランチ（main）宛ての PR に起点を渡さず、I2・port を当てない。pr のステップも止めない。"""
    git(repo, "remote", "set-head", "origin", "develop")
    commit(repo, "plugins/ndf/a.py")
    commit(repo, "plugins/ndf-next/a.py")
    ok, _, _, cur = run_step(monkeypatch, repo, f"前の本文\n\n{PR_FOOTER}\n")
    assert ok and "marks_missing" not in cur
