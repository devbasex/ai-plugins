"""承認の後に変わった対象: 文書だけならリリース承認ゲートを出し直さない（#3020）・承認で見た head を引く口（#2147）。"""

from __future__ import annotations

import json
import subprocess

from ndf.progress import gate, model, run

BY = "human:reviewer"


def _approve(st, tid, q, head, key="k-release", value="approved"):
    return gate.answer(st, tid, gate.Answer(q.id, value, "出してよい", BY, key, seen=gate.Target(head=head)))


def test_a_docs_only_range_after_the_release_approval_does_not_ask_again(st, new_table, release_question):
    tid = new_table()
    _approve(st, tid, release_question(tid), "h1")
    moved = lambda q: gate.Target(head="h2")  # noqa: E731
    why = gate.recheck(st, tid, moved, lambda seen, now: (seen, now) == ("h1", "h2"))
    assert why is not None and "文書だけ" in why
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    # マージの側が照らす head は、出し直さずに進めた今の head
    assert gate.approved_head(run.read(st, tid), "release", st.root) == "h2"
    assert gate.approved_head(run.read(st, tid), "release") == "h1"


def test_a_range_with_code_after_the_release_approval_asks_again(st, new_table, release_question):
    tid = new_table()
    old = release_question(tid)
    _approve(st, tid, old, "h1")
    why = gate.recheck(st, tid, lambda q: gate.Target(head="h2"), lambda seen, now: False)
    assert why is not None and "対象が変わった" in why
    new = gate.waiting(run.read(st, tid))
    assert new is not None and new.id != old.id and new.gate == "release"


def test_the_design_gate_asks_again_even_for_docs_only(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    _approve(st, tid, q, "h1")
    gate.recheck(st, tid, lambda q: gate.Target(head="h2"), lambda seen, now: True)
    assert gate.waiting(run.read(st, tid)) is not None


def _commit(repo, name, text):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", name]
    subprocess.run(cmd, check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_git_docs_only_splits_the_range_like_the_sprint_diff(repo):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text('{"docs": {"behavior": ["**/skills/**/*.md"]}}')
    base = _commit(repo, "src/a.py", "x = 1\n")
    doc = _commit(repo, "docs/a.md", "# a\n")
    skill = _commit(repo, "skills/s/SKILL.md", "# s\n")
    code = _commit(repo, "src/b.py", "y = 2\n")
    docs_only = gate.git_docs_only(repo)
    assert docs_only(base, doc) is True
    assert docs_only(doc, skill) is False  # 振る舞いを決める文書
    assert docs_only(skill, code) is False
    assert docs_only(code, base) is False  # 祖先でない
    assert docs_only("0" * 40, doc) is False  # 読めない


def test_git_docs_only_asks_again_when_behavior_docs_are_not_declared(repo):
    base = _commit(repo, "src/a.py", "x = 1\n")
    doc = _commit(repo, "docs/a.md", "# a\n")
    skill = _commit(repo, "skills/s/SKILL.md", "# s\n")
    docs_only = gate.git_docs_only(repo)
    assert docs_only(base, doc) is False  # 振る舞いを決める文書が分からない
    assert docs_only(doc, skill) is False


def test_the_approved_head_is_the_head_seen_by_the_last_release_answer(st, repo, new_table, capsys, release_question):
    tid = new_table()
    q = release_question(tid)
    assert gate.approved_head(run.read(st, tid), "release") is None
    _approve(st, tid, q, "h1")
    assert gate.approved_head(run.read(st, tid), "release") == "h1"
    assert gate.approved_head(run.read(st, tid), "design") == "d1"
    assert gate.main(["head", tid]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["head"] == "h1"


def test_there_is_no_approved_head_after_a_rejection(st, new_table, release_question):
    tid = new_table()
    _approve(st, tid, release_question(tid), "h1", value="rejected")
    assert gate.approved_head(run.read(st, tid), "release") is None
    assert model.gate_label("release")


def test_a_service_answer_that_is_not_counted_is_no_approval_even_after_a_human_one(st, new_table, release_question):
    # #3959 の決定 3: リリース承認ゲートの service:mvv の回答は承認にしない。前の人の承認へさかのぼらない
    tid = new_table()
    _approve(st, tid, release_question(tid), "h1")
    v = run.read(st, tid)
    payload = {"answer": "approved", "step": "release-gate", "kind": "listen", "notes": None, "by": model.MVV_SERVICE,
               "gate": "release", "gate_id": "x", "seen": {"head": "h2", "material_sha256": None}, model.MVV_KEY: {"holds": []}}  # fmt: skip
    run._write(st, tid, model.EventDraft(run.ANSWERED, run.CONDUCTOR, "デプロイ", reason="古い NDF", payload=payload, rev=v.rev))
    assert gate.approved_head(run.read(st, tid), "release") is None
