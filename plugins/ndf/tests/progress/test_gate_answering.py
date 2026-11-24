"""工程の承認ゲートの回答が、記録の前にマージの承認の証跡を書く（#4409 の決定 7）。"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.adapters.github import GitHubError
from ndf.core import merge_approval
from ndf.progress import command, gate, model, run
from ndf.progress.gate import asking

import scenes

HEAD = "a" * 40


def _lanes():
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).parent / "lanes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _lanes()


@pytest.fixture
def env(repo):
    values = lanes.values(repo, "sprint/s1")
    return run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=lambda r, cwd, work_dir, answer: lanes.ok_result(r, values),
        prepare=lambda r, work_dir, cwd: None,
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda _s: None,
    )


def _target(production: bool = True, base: str = "main", head: str = HEAD) -> gate.Target:
    return gate.Target(head, None, "o/r", 7, base, production)


def _asked(st, repo, env, capsys, production: bool = True) -> gate.Question:
    """standard の設計承認ゲートまで流し、提示した承認の対象（設計 PR 7・main 宛て）を承認資料の隣に残した質問。"""
    code = command.main(["new", "s1", "--mode", "standard", "--issues", "4409"])
    tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
    assert code == 0
    lanes.turn(st, tid, repo, env)
    v = run.read(st, tid)
    q = replace(gate.question(v, run.decide(v)), target=_target(production))
    asking.write_material(st.root, v, q)
    return q


class Posts:
    """証跡を書く口の偽物。書いた行を `rows` に残し、同じ記録の識別子は 2 度書かない。`fail` なら書けない。"""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[merge_approval.MergeRow] = []
        self.fail = fail

    def __call__(self, row: merge_approval.MergeRow) -> bool:
        if self.fail:
            raise GitHubError("届かない", unreached=True)
        if any(r.record_id == row.record_id for r in self.rows):
            return False
        self.rows.append(row)
        return True


def _give(st, q: gate.Question, value: str = "approved", by: str = "human", key: str = "k1", post=None, observe=None) -> dict:
    a = gate.Answer(q.id, value, "回答した", by, key, seen=gate.Target(head=HEAD))
    return gate.answer(st, q.machine_id, a, observe or (lambda _q: _target()), post or Posts())


def _answered(st, q: gate.Question) -> list:
    return [e for e in run.read(st, q.machine_id).events if e.draft.kind == run.ANSWERED]


def _the_answer_writes_the_trail_before_it_is_recorded(repo, st, env, capsys, value):
    q = _asked(st, repo, env, capsys)
    posts = Posts()
    _give(st, q, value, post=posts)
    (row,) = posts.rows
    assert (row.answer, row.by, row.target, row.production, row.record_id) == (
        value,
        "user",
        merge_approval.MergeTarget("o/r", 7, "main", HEAD),
        True,
        q.id,
    )
    (ev,) = _answered(st, q)
    # 回答の事象の seen は承認の対象を持つ（デプロイのマージが読む）
    seen = run.event_payload(ev)["seen"]
    assert (seen["repo"], seen["pr"], seen["base"], seen["head"], seen["production"]) == ("o/r", 7, "main", HEAD, True)


def _a_failed_post_does_not_record_and_a_retry_recovers(repo, st, env, capsys):
    """受け入れ条件 5（証跡の書き込みの失敗）: 書けなければ回答を記録しない。送り直すと証跡は 1 件で、回答が記録される。"""
    q = _asked(st, repo, env, capsys)
    with pytest.raises(GitHubError):
        _give(st, q, post=Posts(fail=True))
    assert _answered(st, q) == []
    posts = Posts()
    _give(st, q, post=posts)
    _give(st, q, post=posts)
    assert len(posts.rows) == 1 and len(_answered(st, q)) == 1


def _a_recorded_answer_without_its_trail_writes_it_on_resend(repo, st, env, capsys):
    q = _asked(st, repo, env, capsys)
    _give(st, q, post=lambda row: False)
    posts = Posts()
    _give(st, q, post=posts)
    assert [r.record_id for r in posts.rows] == [q.id]


def _an_answer_not_by_the_user_is_refused_for_a_production_merge(repo, st, env, capsys, by):
    """受け入れ条件 5（回答の記録を経ない・organizer の中継）: 本番へ反映されるマージの承認は、利用者の回答でなければ断る。"""
    q = _asked(st, repo, env, capsys)
    posts = Posts()
    with pytest.raises(model.Refused):
        _give(st, q, by=by, post=posts)
    assert posts.rows == [] and _answered(st, q) == []


def _service_mvv_and_non_production_answers_write_no_trail(repo, st, env, capsys):
    q = _asked(st, repo, env, capsys, production=False)
    posts = Posts()
    _give(st, q, by="service:factory", post=posts, observe=lambda _q: _target(False))
    assert posts.rows == [] and len(_answered(st, q)) == 1


def _a_base_changed_after_the_question_is_refused(repo, st, env, capsys):
    """受け入れ条件 5（質問の後にマージ先が変わった）: 提示した承認の対象と今の対象が違えば、証跡を書かずに断る。"""
    q = _asked(st, repo, env, capsys)
    posts = Posts()
    with pytest.raises(model.Refused, match="承認の対象が変わった"):
        _give(st, q, post=posts, observe=lambda _q: _target(base="release"))
    assert posts.rows == [] and _answered(st, q) == []


def _a_head_moved_while_waiting_refuses_the_old_material_and_rewrites_it(repo, st, env, capsys):
    """受け入れ条件 5（待ちの間に HEAD が動いた）: 資料が古い HEAD のままなら新しい HEAD への回答も断る。
    提示し直すと資料と提示した対象が新しい HEAD で書き直され、その回答は新しい HEAD の証跡になる。"""
    q = _asked(st, repo, env, capsys)
    moved = "f" * 40
    posts = Posts()
    a = gate.Answer(q.id, "approved", "回答した", "human", "k1", seen=gate.Target(head=moved))
    with pytest.raises(model.Refused, match="承認の対象が変わった"):
        gate.answer(st, q.machine_id, a, lambda _q: _target(head=moved), posts)
    assert posts.rows == [] and _answered(st, q) == []
    v = run.read(st, q.machine_id)
    asking.write_material(st.root, v, replace(q, target=_target(head=moved)))
    assert asking.read_presented(st.root, q).head == moved
    assert moved in asking.material_path(st.root, q).read_text(encoding="utf-8")
    gate.answer(st, q.machine_id, a, lambda _q: _target(head=moved), posts)
    assert [r.target.head for r in posts.rows] == [moved]


def _the_material_says_the_merge_reaches_production(repo, st, env, capsys):
    """受け入れ条件 6（承認資料）: 本番へ反映され得る承認の対象の承認資料は、その行を持つ。持たない対象は行が無い。"""
    q = _asked(st, repo, env, capsys)
    v = run.read(st, q.machine_id)
    assert asking.PRODUCTION_LINE in asking.material(v, q)
    assert asking.PRODUCTION_LINE not in asking.material(v, replace(q, target=_target(False)))
    assert asking.read_presented(st.root, q) == _target()


def _an_old_seen_reads_as_a_target_without_binding():
    old = gate.Target.from_dict({"head": HEAD, "material_sha256": None})
    assert old.binding() is None and old.production is None


def _stale_routes_carry_the_retry_and_the_next(repo, st, env, capsys):
    """決定 4・5: approval_stale で止まった工程の結果は、retry（理由 approval_stale）と next を持つ。"""
    routes, nxt = run.stale_routes("m1", "設計")
    assert routes["retry"]["argv"][-4:] == ["m1", "設計", "--reason", "approval_stale"]
    assert nxt["argv"][-2:] == ["next", "m1"]


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        *[
            scenes.scene(
                _the_answer_writes_the_trail_before_it_is_recorded,
                id=scenes.label("the_answer_writes_the_trail_before_it_is_recorded", i, (v,)),
                value=v,
            )
            for i, v in enumerate(["approved", "rejected"])
        ],
        scenes.scene(_a_failed_post_does_not_record_and_a_retry_recovers, id="a_failed_post_does_not_record_and_a_retry_recovers"),
        scenes.scene(
            _a_recorded_answer_without_its_trail_writes_it_on_resend, id="a_recorded_answer_without_its_trail_writes_it_on_resend"
        ),
        *[
            scenes.scene(
                _an_answer_not_by_the_user_is_refused_for_a_production_merge,
                id=scenes.label("an_answer_not_by_the_user_is_refused_for_a_production_merge", i, (v,)),
                by=v,
            )
            for i, v in enumerate(["conductor", "service:factory", "organizer"])
        ],
        scenes.scene(_service_mvv_and_non_production_answers_write_no_trail, id="service_mvv_and_non_production_answers_write_no_trail"),
        scenes.scene(_a_base_changed_after_the_question_is_refused, id="a_base_changed_after_the_question_is_refused"),
        scenes.scene(
            _a_head_moved_while_waiting_refuses_the_old_material_and_rewrites_it,
            id="a_head_moved_while_waiting_refuses_the_old_material_and_rewrites_it",
        ),
        scenes.scene(_the_material_says_the_merge_reaches_production, id="the_material_says_the_merge_reaches_production"),
        scenes.scene(_an_old_seen_reads_as_a_target_without_binding, id="an_old_seen_reads_as_a_target_without_binding"),
        scenes.scene(_stale_routes_carry_the_retry_and_the_next, id="stale_routes_carry_the_retry_and_the_next"),
    ],
)
def test_gate_answering_scenes(request, scene, values):
    """承認ゲートの回答とマージの承認の証跡の場面（#4409 の決定 7）。"""
    scenes.play(request, scene, values)
