"""承認ゲートの結果の差し替え・承認資料・回答を読む前の対象の見直し（#3295。3364-2・3390-2・#2444・#2554）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.adapters.github import GitHubError
from ndf.contracts import result
from ndf.progress import gate, invoke, model, run

BY = "service:factory"


def _asked(st, repo, tid, observe):
    v = run.read(st, tid)
    return gate.present(st, tid, repo, run.report(v, run.decide(v)), observe)


def _with_gaps(env):
    """design-review の結果に扱われなかった指摘（欠け）を載せる `Env`。ok の結果は欠けを outputs.gaps に持つ（結果のコントラクト）。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design-review":
            gaps = [{"kind": "unhandled_finding", "name": "f-1", "reason": "直したとも見送ったとも決めていない"}]
            return invoke.finished(r, result.result("plan", "ok", "レビューを終えた", outputs={"gaps": gaps}))
        return env.invoker(r, cwd, work_dir, answer)

    return run.Env(**{**vars(env), "invoker": invoker})


def test_the_gate_result_of_progress_is_the_question_with_the_material(st, env, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid, _with_gaps(env))
    out = _asked(st, repo, tid, lambda q: gate.Target(head="h1", material=None))
    assert out["status"] == "gate" and result.errors(out, 10) == []
    meta = out["metrics"]["gate"]
    assert meta["kind"] == "design" and meta["head"] == "h1" and out["metrics"]["stage"] == "設計"
    assert out["routes"]["approved"]["argv"][:2] == ["ndf", "answer"]
    text = open(out["presentation_path"], encoding="utf-8").read()
    assert "f-1（直したとも見送ったとも決めていない）" in text and meta["id"] in text
    # 書いた承認資料は書き直さない（回答した者が見た sha256 が変わらない）
    again = _asked(st, repo, tid, gate.github_observe(repo, st.root, lambda: _hub("h1")))
    assert again["metrics"]["gate"]["material_sha256"] == gate.material_sha256(Path(out["presentation_path"]))


def test_a_result_that_is_not_a_listen_gate_is_left_as_it_is(st, repo, new_table):
    tid = new_table()
    v = run.read(st, tid)
    out = run.report(v, run.decide(v))
    assert gate.present(st, tid, repo, out, gate.no_observe) is out


def _hub(head):
    return SimpleNamespace(pull=lambda n: SimpleNamespace(head_sha=head))


def _answered(st, tid, head):
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    gate.answer(st, tid, gate.Answer(q.id, "approved", "見た", BY, "k-1", seen=gate.Target(head=head)))
    return q


def test_an_answer_whose_target_moved_before_it_is_read_goes_back_to_waiting(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    old = _answered(st, tid, "h1")
    assert gate.recheck(st, tid, lambda q: gate.Target(head="h1")) is None
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    why = gate.recheck(st, tid, lambda q: gate.Target(head="h2"))
    assert why is not None and "h2" in why
    new = gate.waiting(run.read(st, tid))
    assert new is not None and new.id != old.id and new.stage == "設計"


def test_the_design_gate_asks_about_the_design_pr_of_the_finished_step(st, repo, new_table, flow):
    # 設計 PR は工程の途中ではステップの記録にしか無い（工程の出力は工程が終わる時に書く）
    tid = new_table()
    flow.turn(tid)
    v = run.read(st, tid)
    assert "design_pr" not in v.context()
    q = gate.waiting(v, repo="o/r")
    assert q is not None and q.pr == 7 and q.pr_url == "https://github.com/o/r/pull/7"


def test_github_observe_reads_the_head_and_refuses_when_it_cannot(st, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.pr == 7
    assert gate.github_observe(repo, st.root, lambda: _hub("abc"))(q) == gate.Target(head="abc")

    def broken():
        def pull(n):
            raise GitHubError("届かない", unreached=True)

        return SimpleNamespace(pull=pull)

    with pytest.raises(model.Refused, match="head を読めない"):
        gate.github_observe(repo, st.root, broken)(q)


# --- プランの承認資料の「MVV 判定」の節（#3959 の受け入れ条件 3） ---------------------------------------------

JUDGED = {
    "gate": "design",
    "skip": False,
    "verdict": {"verdict": "follow", "reasons": ["理由その 1", "理由その 2"], "boundary": ["レッドラインの本文"], "basis": ["Value 1"]},
    "why": ["進め方 normal は承認ゲートを MVV 判定で省かない"],
    "holds": ["#7: Value 3 の見送り（保留の本文）"],
    "noted": True,
    "note_url": "https://github.com/o/r/pull/7#issuecomment-1",
    "pr": 7,
    "head": "h1",
}


def _judging(env, judged):
    """設計の工程の判定のステップ（design-mvv）が `judged` を出す `Env`。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design-mvv":
            return invoke.finished(r, result.result("plan", "ok", "判定した", outputs={"mvv_gate": judged}))
        return env.invoker(r, cwd, work_dir, answer)

    return run.Env(**{**vars(env), "invoker": invoker})


def _material_of(out):
    return open(out["presentation_path"], encoding="utf-8").read()


def test_the_plan_material_carries_the_mvv_verdict_of_the_waiting_stage(st, env, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    text = _material_of(_asked(st, repo, tid, lambda q, **_: gate.Target(head="h1")))
    assert "## MVV 判定" in text and "判定した PR: #7・head h1" in text
    for line in ("理由その 2", "レッドラインの本文", "Value 1", "進め方 normal は", "Value 3 の見送り（保留の本文）", JUDGED["note_url"]):
        assert line in text


def test_a_push_after_the_verdict_writes_a_new_head_material_without_the_old_verdict(st, env, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    first = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h1"))
    moved = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h2"))
    assert moved["presentation_path"] != first["presentation_path"] and Path(first["presentation_path"]).exists()
    text = _material_of(moved)
    assert "PR #7・head h2 の判定はまだ無い" in text and "理由その 2" not in text and "保留の本文" not in text
    assert moved["metrics"]["gate"]["material_sha256"] == gate.material_sha256(Path(moved["presentation_path"]))


def test_showing_the_same_head_again_reuses_the_material_and_its_hash(st, env, repo, new_table, flow, monkeypatch):
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    first = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h1"))
    sha = first["metrics"]["gate"]["material_sha256"]
    calls = []

    def observe(q, **kw):
        calls.append(kw)
        return gate.Target(head="h1", material=sha)

    from ndf.progress.gate import entry

    monkeypatch.setattr(entry, "write_material", lambda *a, **k: (_ for _ in ()).throw(AssertionError("書き直した")))
    monkeypatch.setattr(entry, "material_sha256", lambda *a: (_ for _ in ()).throw(AssertionError("読み直した")))
    again = _asked(st, repo, tid, observe)
    assert len(calls) == 1 and again["metrics"]["gate"]["material_sha256"] == sha
    assert again["presentation_path"] == first["presentation_path"]


def test_an_answer_with_the_hash_of_the_old_head_material_is_refused(st, env, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    first = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h1"))
    meta = first["metrics"]["gate"]
    old = gate.Target(head="h1", material=meta["material_sha256"])
    with pytest.raises(model.Refused, match="対象が変わった"):
        gate.answer(st, tid, gate.Answer(meta["id"], "approved", "見た", BY, "k-old", seen=old), lambda q, **_: gate.Target(head="h2"))


def test_the_plan_material_has_no_citation_errors():
    # プランの資料は根拠の行を写さない。cite と同じ確かめに通して誤りが 0 件であることを固定する
    from ndf.domains.records import mvv, mvv_decide

    out = {**JUDGED, "holds": ["#7: Value 3 の見送り"]}
    body = "\n".join(["# 承認資料", "", "## MVV 判定", "", *gate.mvv_lines(out, "design", 7, "h1")]) + "\n"
    fenced, decided = mvv.fenced_lines(body), mvv.decision_lines(body)
    rows = [("material.md", no, line, no in decided) for no, line in enumerate(body.splitlines(), 1) if no not in fenced]
    assert mvv_decide.citation_errors(rows, {11: ["Value 1", "Value 3"]}) == []


def _mode(path):
    return Path(path).stat().st_mode & 0o777


def _waiting_question(st, tid, head="h1"):
    v = run.read(st, tid)
    q = gate.waiting(v)
    from dataclasses import replace

    return v, replace(q, target=gate.Target(head))


def test_write_material_makes_an_owner_only_file_in_an_owner_only_directory(st, repo, new_table, flow, monkeypatch, tmp_path):
    import os

    tid = new_table()
    flow.turn(tid)
    v, q = _waiting_question(st, tid)
    old = os.umask(0o022)
    try:
        path = gate.write_material(tmp_path / "root", v, q)
    finally:
        os.umask(old)
    assert path.name == f"{q.id}-h1.md" and _mode(path) == 0o600 and _mode(path.parent) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    # 既にある資料は書き直さず、権限も変えない
    os.chmod(path, 0o640)
    before = path.read_bytes()
    assert gate.write_material(tmp_path / "root", v, q) == path and path.read_bytes() == before and _mode(path) == 0o640


def test_write_material_keeps_the_material_another_call_placed_first(st, repo, new_table, flow, monkeypatch, tmp_path):
    import os

    tid = new_table()
    flow.turn(tid)
    v, q = _waiting_question(st, tid)
    path = gate.material_path(tmp_path, q)
    real_link = os.link

    def raced(src, dst):
        Path(dst).write_text("先に置いた資料\n", encoding="utf-8")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", raced)
    assert gate.write_material(tmp_path, v, q) == path
    assert path.read_text(encoding="utf-8") == "先に置いた資料\n" and [p.name for p in path.parent.iterdir()] == [path.name]


def test_an_unrestricted_store_writes_counts_and_the_comment_link_instead_of_the_text(st, env, repo, new_table, flow):
    st.private = False
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    text = _material_of(_asked(st, repo, tid, lambda q, **_: gate.Target(head="h1")))
    assert "理由 2 件・レッドライン 1 件" in text and f"今回の判定のコメント {JUDGED['note_url']} で読めます" in text
    assert "理由その 2" not in text and "保留の本文" not in text and model.MVV_WITHHELD not in text
