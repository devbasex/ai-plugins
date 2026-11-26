"""承認ゲートの結果の差し替え・承認資料・回答を読む前の対象の見直し（#3295。3364-2・3390-2・#2444・#2554）。"""

from __future__ import annotations

import json

from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.adapters.github import FileAt, GitHubError
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
    out = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h1", material=None))
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


def _hub(head, text=None):
    """PR（HEAD `head`・develop 宛て）とマージ先の設定（本文 `text`。省けば本番チャネルでない設定）を読む偽の GitHub。"""
    config = text if text is not None else json.dumps({"branch_strategy": {"branches": {"base": "develop", "production": "main"}}})
    return SimpleNamespace(
        repo="o/r",
        pull=lambda n: SimpleNamespace(head_sha=head, base_ref="develop", number=n, body="本文", title="題"),
        branch_file=lambda base, path: FileAt("o/r", config, "c1"),
    )


def _readings(st, tid) -> int:
    """設計の工程で読み手役のステップが済んだ回数（未了に戻した後に打ち直すと増える）。"""
    events = run.read(st, tid).events
    done = [e for e in events if e.draft.kind == "step_recorded" and e.draft.payload["status"] == "finished"]
    return sum(1 for e in done if e.draft.payload["step"] == "design-reading")


@pytest.mark.parametrize(
    "now",
    [
        gate.Target("h2", body="b1", title="t1"),  # head が動いた
        gate.Target("h1", body="b2", title="t1"),  # 本文だけが変わった（#4446）
        gate.Target("h1", body="b1", title="t2"),  # 題だけが変わった（#4446）
    ],
)
def test_an_answer_whose_target_moved_before_it_is_read_goes_back_to_waiting(st, new_table, flow, now):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and _readings(st, tid) == 1
    seen = gate.Target("h1", body="b1", title="t1")
    gate.answer(st, tid, gate.Answer(q.id, "approved", "見た", BY, "k-1", seen=seen))
    assert gate.recheck(st, tid, lambda _q, **_: seen) is None
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    why = gate.recheck(st, tid, lambda _q, **_: now)
    assert why is not None and "見た値" in why
    # #4446: 待ちへ戻すときに同じトランザクションで読み手役のステップを未了に戻し、次の駆動が今の対象で打ち直してから待つ
    assert "design-reading" not in run.step_progress(run.read(st, tid), "設計").done
    flow.turn(tid)
    new = gate.waiting(run.read(st, tid))
    assert new is not None and new.id != q.id and new.stage == "設計" and _readings(st, tid) == 2
    # MVV 判定で省いた回答は head だけを見るので、本文と題の値が無い見た対象は本文と題で断らない
    assert now.differs(gate.Target(now.head)) == []


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
    # 同じ 1 回の取得で本文と題の sha256 も読む（#4446）
    texts = {"body": gate.text_sha256("本文"), "title": gate.text_sha256("題")}
    assert gate.github_observe(repo, st.root, lambda: _hub("abc"))(q) == gate.Target("abc", None, "o/r", 7, "develop", False, **texts)
    # マージ先が本番チャネルで設定を読めなければ、本番へ反映され得るとして読む（#4409）
    assert gate.github_observe(repo, st.root, lambda: _hub("abc", "壊れた"))(q).production is True

    def broken():
        def pull(n):
            raise GitHubError("届かない", unreached=True)

        return SimpleNamespace(pull=pull)

    with pytest.raises(model.Refused, match="head を読めない"):
        gate.github_observe(repo, st.root, broken)(q)


# --- プランの承認資料の「MVV 判定」の欄と head ごとの資料（#3959 の受け入れ条件 3） ------------------------------------

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
FREE = ("理由その 2", "レッドラインの本文", "進め方 normal は", "保留の本文")


def _judging(env, judged):
    """設計の工程の判定のステップ（design-mvv）が `judged` を出す `Env`。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design-mvv":
            return invoke.finished(r, result.result("plan", "ok", "判定した", outputs={"mvv_gate": judged}))
        return env.invoker(r, cwd, work_dir, answer)

    return run.Env(**{**vars(env), "invoker": invoker})


def _material_of(out):
    return Path(out["presentation_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("private", [True, False])
def test_the_plan_material_carries_the_verdict_of_the_head_it_was_made_for(st, env, repo, new_table, flow, monkeypatch, private):
    from ndf.progress.gate import asking

    st.private = private
    tid = new_table()
    flow.turn(tid, _judging(env, JUDGED))
    first = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h1"))
    text = _material_of(first)
    assert "## MVV 判定" in text and "判定した PR: #7・head h1" in text and "Value 1" in text
    if private:
        assert all(w in text for w in FREE) and JUDGED["note_url"] in text
    else:
        assert "理由 2 件・レッドライン 1 件" in text and f"コメント {JUDGED['note_url']} で読めます" in text
        assert not any(w in text for w in FREE) and model.MVV_WITHHELD not in text
    # 同じ head で表示し直す: 資料を書き直さず、同じハッシュを返す（observe は 1 回だけ）
    sha, calls = first["metrics"]["gate"]["material_sha256"], []
    with monkeypatch.context() as m:
        m.setattr(asking.os, "link", lambda *a: pytest.fail("書き直した"))
        again = _asked(st, repo, tid, lambda q, **kw: calls.append(kw) or gate.Target(head="h1", material=sha))
    assert (
        len(calls) == 1 and again["metrics"]["gate"]["material_sha256"] == sha and again["presentation_path"] == first["presentation_path"]
    )
    # 判定の後に push された: 新しい head の資料に前の head の判定を載せず、前の資料は残す
    moved = _asked(st, repo, tid, lambda q, **_: gate.Target(head="h2"))
    text = _material_of(moved)
    assert moved["presentation_path"] != first["presentation_path"] and Path(first["presentation_path"]).exists()
    assert "PR #7・head h2 の判定はまだ無い" in text and not any(w in text for w in FREE)
    assert moved["metrics"]["gate"]["material_sha256"] == gate.material_sha256(Path(moved["presentation_path"]))
    # 前の head の資料のハッシュで回答すると断る
    old = gate.Target(head="h1", material=sha)
    with pytest.raises(model.Refused, match="対象が変わった"):
        gate.answer(
            st,
            tid,
            gate.Answer(first["metrics"]["gate"]["id"], "approved", "見た", BY, "k-old", seen=old),
            lambda q, **_: gate.Target(head="h2"),
        )


def test_write_material_places_an_owner_only_file_once_and_keeps_the_one_placed_first(st, repo, new_table, flow, monkeypatch, tmp_path):
    import os
    from dataclasses import replace

    tid = new_table()
    flow.turn(tid)
    v = run.read(st, tid)
    q = replace(gate.waiting(v), target=gate.Target("h1"))
    old = os.umask(0o022)
    try:
        path = gate.write_material(tmp_path / "a", v, q)
    finally:
        os.umask(old)
    mode = lambda p: p.stat().st_mode & 0o777  # noqa: E731
    assert path.name == f"{q.id}-h1.md" and mode(path) == 0o600 and mode(path.parent) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    # 既にある資料は書き直さず、権限も変えない
    os.chmod(path, 0o640)
    before = path.read_bytes()
    assert gate.write_material(tmp_path / "a", v, q) == path and path.read_bytes() == before and mode(path) == 0o640
    # 置く直前に別の呼び出しが同じ資料を置いた: 例外を出さず先の資料を使い、一時ファイルを残さない
    raced = gate.material_path(tmp_path / "b", q)
    real_link = os.link

    def link(src, dst):
        Path(dst).write_text("先に置いた資料\n", encoding="utf-8")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    assert gate.write_material(tmp_path / "b", v, q) == raced
    assert raced.read_text(encoding="utf-8") == "先に置いた資料\n" and [p.name for p in raced.parent.iterdir()] == [raced.name]
