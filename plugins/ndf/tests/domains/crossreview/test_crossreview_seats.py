"""#3955: cross-review の空いた席を claude の読むだけの起動で埋める（相手役と修正担当は偽物、git は一時の origin で本物を打つ）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

import pytest

from ndf.adapters import agents, git
from ndf.contracts import result
from ndf.domains.crossreview import cli, drive
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, run_cli
from .test_crossreview_run import FAIL, FAILED_WITH_FINDINGS, NO_FINDINGS, PR, Fake, _run, _state, seat_of


def _allow(wt: Path, *allowed: str) -> None:
    """メインディレクトリの `.ndf/runtimes.json` の `allowed` を書く。"""
    from ndf.core.converge import location

    main = location.main_dir(git.Git(wt))
    (main / ".ndf").mkdir(exist_ok=True)
    (main / ".ndf" / "runtimes.json").write_text(json.dumps({"allowed": list(allowed)}), encoding="utf-8")


def _run_seats(capsys, monkeypatch, fake: Fake, wt: Path, *extra: str) -> tuple[int, dict]:
    """`--reviewer` を渡さない（または `extra` で渡す）起動。"""
    monkeypatch.setattr(cli, "LAUNCH", fake)
    argv = ["run", "--pr", str(PR), "--state-dir", str(fake.state_dir), "--base", "origin/main", "--worktree", str(wt), "--branch", BRANCH]
    code, got = run_cli(capsys, *argv, *extra)
    assert result.errors(got, code) == []
    return code, got


def _seat_specs(fake: Fake) -> list[agents.Launch]:
    return [s for s in fake.launches if s.agent == "claude" and "--restricted" in s.argv]


def test_issue_3955_allowed_claude_and_codex_seats_codex_and_claude(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 5: allowed が claude と codex なら、codex と claude の 2 者でレビューし、起動の前に埋めたことが残る。"""
    _allow(wt, "claude", "codex")
    fake = Fake(tmp_path / "st", {"codex": [[]], "claude": [[]]})
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert sorted(fake.calls) == ["claude", "codex"] and fake.seat_calls == ["claude"]
    record = _state(fake.state_dir)["rounds"][0]
    assert record["reviewers"] == ["codex", "claude"]
    assert record["filled"] == [{"seat": "claude", "for": None, "reason": "allowed の相手役 1（codex）"}]
    assert any("起動の前" in w and "allowed の相手役 1（codex）" in w for w in got["warnings"])


def test_issue_3955_allowed_claude_only_seats_two_claudes(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 6・決定 3: allowed が claude だけでも止まらず、claude と claude-2 でレビューし、codex と kiro を起動しない。
    claude-2 の起動はランタイム claude で、出力の名前は round<n>-claude-2。"""
    _allow(wt, "claude")
    fake = Fake(tmp_path / "st", {"claude": [[]], "claude-2": [[]]})
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert sorted(fake.seat_calls) == ["claude", "claude-2"] and set(fake.calls) == {"claude"}
    assert sorted(s.paths.out.name for s in _seat_specs(fake)) == ["round1-claude-2.out", "round1-claude.out"]
    assert _state(fake.state_dir)["rounds"][0]["reviewers"] == ["claude", "claude-2"]


def test_issue_3955_review_scope_uses_the_same_seats(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """review-scope も run と同じ席で範囲を決め、allowed が claude だけでも invalid で止まらない。`--reviewer claude` は invalid。"""
    _allow(wt, "claude")
    argv = ["review-scope", str(wt), "--state-dir", str(tmp_path / "st"), "--pr", str(PR), "--purpose", "design", "--base", "origin/main"]
    code, got = run_cli(capsys, *argv)
    assert (code, got["status"]) == (0, "ok")
    run_argv = ["run", "--pr", "1", "--state-dir", str(tmp_path / "st"), "--base", "origin/main", "--worktree", str(wt)]
    assert cli._run_config(cli._parser().parse_args(run_argv)).reviewers == ("claude", "claude-2")
    code, got = run_cli(capsys, *argv, "--reviewer", "claude")
    assert got["cause"]["kind"] == "invalid"


def test_issue_3955_seat_claude_reads_only_this_runs_inputs(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 7・8: 席の claude は最小構成の読むだけの起動で、修正担当の会話を持たない。--add-dir は実行ごとに新しく作った
    review-input-* だけを指し、リクエストの文が指す資料はすべてその下にある。前の実行の資料とリンクは使わず、実行の終わりに消える。"""
    _allow(wt, "claude", "codex")
    state_dir = tmp_path / "st"
    stale = state_dir / "review-input-old"
    stale.mkdir(parents=True)
    (stale / "round1.diff").write_text("前の PR の差分", encoding="utf-8")
    (state_dir / "review-input-link").symlink_to(stale)
    fake = Fake(state_dir, {"codex": [[]], "claude": [[]]})
    _run_seats(capsys, monkeypatch, fake, wt)
    spec = _seat_specs(fake)[0]
    argv = list(spec.argv)
    for word in ("--no-session-persistence", "--restricted"):
        assert word in argv
    assert "acceptEdits" not in argv and "--resume" not in argv and "--session-id" not in argv
    assert argv[argv.index("--tools") + 1] == argv[argv.index("--allowed-tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    place = Path(argv[argv.index("--add-dir") + 1])
    assert argv.count("--add-dir") == 1 and place.parent == state_dir and place.name.startswith(drive.INPUT_PREFIX)
    assert place not in (stale, state_dir / "review-input-link")
    named = re.findall(r"(/\S+?\.(?:diff|txt))", spec.argv[-1])
    assert named and all(Path(p).parent == place for p in named)
    assert not place.exists() and stale.is_dir()


def test_issue_3955_inputs_are_dropped_when_the_run_stops(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [FAIL, FAIL], "kiro": [FAIL, FAIL]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "precondition"
    assert list(fake.state_dir.glob(f"{drive.INPUT_PREFIX}*")) == []


def _refuse(monkeypatch, refused: dict[str, str]) -> None:
    from ndf.core.converge import participants

    monkeypatch.setattr(participants, "refused_runtimes", lambda runtimes: {r: w for r, w in refused.items() if r in runtimes})


def test_issue_3955_refused_reviewer_seat_is_filled_before_the_round(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 15 (a): 始める前の確かめで kiro を断ると、kiro を起動せずに codex と claude でレビューする。"""
    _refuse(monkeypatch, {"kiro": "学習に使う設定"})
    fake = Fake(tmp_path / "st", {"codex": [[]], "claude": [[]]})
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "kiro" not in fake.calls and fake.seat_calls == ["claude"]
    record = _state(fake.state_dir)["rounds"][0]
    assert record["reviewers"] == ["codex", "claude"]
    assert record["filled"] == [{"seat": "claude", "for": "kiro", "reason": "unready: 学習に使う設定"}]


def test_issue_3955_both_refused_reviewers_are_filled_with_two_claudes(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 15 (b)。"""
    _refuse(monkeypatch, {"kiro": "k", "codex": "c"})
    fake = Fake(tmp_path / "st", {})
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert sorted(fake.seat_calls) == ["claude", "claude-2"] and set(fake.calls) == {"claude"}
    assert _state(fake.state_dir)["rounds"][0]["reviewers"] == ["claude", "claude-2"]


@pytest.mark.parametrize(
    ("refused", "extra"),
    [({"claude": "c"}, ()), ({"kiro": "k"}, ("--reviewer", "kiro")), ({"codex": "c"}, ("--reviewer", "codex", "--reviewer", "codex"))],
)
def test_issue_3955_refused_fixer_or_single_named_reviewer_is_precondition(
    wt: Path, tmp_path: Path, capsys, monkeypatch, refused, extra
) -> None:
    """受け入れ条件 15 (c)(d)・名指しの数え方: 修正担当か、1 者だけ名指した相手役が断られると、どの担当も起動せずに止まる。"""
    _refuse(monkeypatch, refused)
    fake = Fake(tmp_path / "st", {})
    code, got = _run_seats(capsys, monkeypatch, fake, wt, *extra)
    assert got["cause"]["kind"] == "precondition" and "担当を起動できない" in got["cause"]["detail"]
    assert fake.calls == []


def test_issue_3955_one_check_per_runtime(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 15 (e): 席が claude と claude-2 でも、学習の設定は claude について 1 回だけ確かめる。"""
    from ndf.adapters import training

    _allow(wt, "claude")
    asked: list[str] = []
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: asked.append(runtime))
    _run_seats(capsys, monkeypatch, Fake(tmp_path / "st", {}), wt)
    assert asked == ["claude"]


def _prompts(fake: Fake, out: str) -> list[str]:
    return [s.argv[-1] for s in fake.launches if s.paths.out.name == out]


def test_issue_3955_unread_kiro_seat_is_filled_by_claude_for_the_rest_of_the_run(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 1〜4: kiro が起動し直しても読めないと、同じラウンドで claude が kiro の席でレビューし、codex と claude の
    指摘で判定する。指摘が無くなれば ok で、注意に欠けた相手役・理由・埋めた席が載る。記録とコメントに codex と claude が載り、
    2 ラウンド目は kiro を起動せず、両者に前の head からの差分を渡す。"""
    fake = Fake(tmp_path / "st", {"codex": [["直す"], []], "kiro": [FAIL, FAIL], "claude": [["claude の指摘"], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert fake.calls.count("kiro") == 2 and fake.seat_calls == ["claude", "claude"]
    assert any("相手役 kiro の席" in w and "failed: 落ちた" in w and "（claude）" in w and w.startswith("round 1") for w in got["warnings"])
    rounds = _state(fake.state_dir)["rounds"]
    filled = [{"seat": "claude", "for": "kiro", "reason": "failed: 落ちた"}]
    assert [(r["reviewers"], r["filled"], r["scope"]) for r in rounds] == [
        (["codex", "claude"], filled, "full"),
        (["codex", "claude"], filled, "since"),
    ]
    findings = _state(fake.state_dir)["findings"]
    assert {f["by"] for f in findings} == {"codex", "claude"}
    since = [p for p in _prompts(fake, "round2-codex.out") + _prompts(fake, "round2-claude.out")]
    assert len(since) == 2 and all(f"{fake.input_dir}/round2.diff" in p and "since" in p for p in since)


def test_issue_3955_report_comment_shows_the_filled_seat(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [FAIL, FAIL], "claude": [[]]})
    _run(capsys, monkeypatch, fake, wt)
    assert "| codex・claude（kiro の席） |" in hub.comments[-1][2]


def test_issue_3955_first_filled_in_round_two_reads_the_whole_diff(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """round 2 で初めて埋めた claude には --base からの全差分と前回の情報を含まないリクエストの文を渡し、codex には前の head からの差分。
    round 2 の記録の範囲は since のままで、round 3 は両者に round 2 の head からの差分を渡す。"""
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], ["二つ目"], []], "kiro": [[], FAIL, FAIL], "claude": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "3")
    assert (code, got["status"]) == (0, "ok")
    rounds = _state(fake.state_dir)["rounds"]
    assert [r["scope"] for r in rounds] == ["full", "since", "since"]
    assert [r["reviewers"] for r in rounds] == [["codex", "kiro"], ["codex", "claude"], ["codex", "claude"]]
    (seat,) = _prompts(fake, "round2-claude.out")
    assert f"{fake.input_dir}/round2-full.diff" in seat and "範囲: full" in seat
    assert "doc.md" in fake.inputs["round2-full.diff"] and "doc.md" not in fake.inputs["round2.diff"]
    assert all(
        f"{fake.input_dir}/round3.diff" in p and "範囲: since" in p
        for p in _prompts(fake, "round3-codex.out") + _prompts(fake, "round3-claude.out")
    )
    assert "kiro" not in [seat_of(s) for s in fake.launches if s.paths.out.name.startswith("round3-")]


def test_issue_3955_seat_claude_failing_is_not_relaunched(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """起動し直しの回数 (a): allowed が claude だけで claude-2 が結果を返さないと、起動し直さずに incomplete_reviewers。"""
    _allow(wt, "claude")
    fake = Fake(tmp_path / "st", {"claude": [[]], "claude-2": [FAIL]})
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    assert fin.INCOMPLETE_REVIEWERS in got["summary"]
    assert sorted(fake.seat_calls) == ["claude", "claude-2"]


def test_issue_3955_claude_seated_in_round_one_is_not_relaunched_in_round_two(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """起動し直しの回数 (b)。"""
    fake = Fake(tmp_path / "st", {"codex": [["直す"], []], "kiro": [FAIL, FAIL], "claude": [[], FAIL]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert fin.INCOMPLETE_REVIEWERS in got["summary"]
    assert fake.seat_calls == ["claude", "claude"] and "kiro" not in [seat_of(s) for s in fake.launches if "round2-" in s.paths.out.name]


@pytest.mark.parametrize(
    ("script", "want", "seats"),
    [
        ({"codex": [FAIL, FAIL], "claude": [FAIL]}, "precondition", []),
        ({"codex": [[]], "claude": [FAIL]}, fin.INCOMPLETE_REVIEWERS, []),
        ({"codex": [FAIL, FAIL], "claude": [[]], "claude-2": [[]]}, "ok", ["claude-2"]),
    ],
)
def test_issue_3955_branch_order_with_a_claude_seat(wt: Path, tmp_path: Path, capsys, monkeypatch, script, want, seats) -> None:
    """分岐の順 (a)〜(c): 席が codex と claude のとき、両方が結果を返さなければ precondition、claude の席だけなら埋めずに
    incomplete_reviewers、codex だけなら codex の席を claude-2 で埋める。claude の席は起動し直さない。"""
    _allow(wt, "claude", "codex")
    fake = Fake(tmp_path / "st", script)
    code, got = _run_seats(capsys, monkeypatch, fake, wt)
    kind = got["status"] if want == "ok" else got["cause"]["kind"] if want == "precondition" else fin.INCOMPLETE_REVIEWERS
    assert kind == want and (want != fin.INCOMPLETE_REVIEWERS or want in got["summary"])
    assert fake.seat_calls == ["claude", *seats]


@pytest.mark.parametrize("extra", [("--reviewer", "codex"), ("--reviewer", "codex", "--reviewer", "codex")])
def test_issue_3955_single_named_reviewer_is_never_filled(wt: Path, tmp_path: Path, capsys, monkeypatch, extra) -> None:
    """受け入れ条件 12・名指しの数え方: 1 者だけ名指して読めなければ precondition で、claude を相手役として起動しない。"""
    fake = Fake(tmp_path / "st", {"codex": [FAIL, FAIL]})
    code, got = _run_seats(capsys, monkeypatch, fake, wt, *extra)
    assert got["cause"]["kind"] == "precondition" and fake.seat_calls == []
    ok = Fake(tmp_path / "st2", {"codex": [[]]})
    code, got = _run_seats(capsys, monkeypatch, ok, wt, *extra)
    assert (code, got["status"]) == (0, "ok") and _state(ok.state_dir)["rounds"][0]["reviewers"] == ["codex"]


@pytest.mark.parametrize("said", [NO_FINDINGS, FAILED_WITH_FINDINGS])
def test_issue_3955_a_result_needs_done_and_a_findings_array(wt: Path, tmp_path: Path, capsys, monkeypatch, said) -> None:
    """結果の有無: 終了は成功でも指摘の配列が無い席と、完了でない状態で終わった席は、有効な出力があっても結果が無い（起動し直し・
    埋める）。`{"findings": []}` で完了した席は成功として埋めない。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [said, said], "claude": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert fake.calls.count("kiro") == 2 and fake.seat_calls == ["claude"]


def _converged_with_kiro_filled(capsys, monkeypatch, wt: Path, state_dir: Path) -> None:
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [FAIL, FAIL], "claude": [[]]}), wt)
    assert _state(state_dir)["rounds"][-1]["verdict"] == fin.CONVERGED


def test_issue_3955_same_head_rerun_reuses_the_filled_round(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """同じ head の打ち直し (a)(e)・始める前の確かめ (f): 前回埋めた席を欠けた相手役の代わりにも数え、どの担当も起動せずに前回の
    結果を返し、注意に kiro の席を claude で埋めたことと理由が載る。kiro を断る設定でも確かめずに返す。"""
    state_dir = tmp_path / "st"
    _converged_with_kiro_filled(capsys, monkeypatch, wt, state_dir)
    _refuse(monkeypatch, {"kiro": "k"})
    again = Fake(state_dir, {})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"], again.calls) == (0, "ok", [])
    assert any(w.startswith("前回の round 1") and "相手役 kiro の席" in w and "failed: 落ちた" in w for w in got["warnings"])
    _allow(wt, "claude", "codex")
    narrowed = Fake(state_dir, {})
    code, got = _run_seats(capsys, monkeypatch, narrowed, wt)
    assert (code, got["status"], narrowed.calls) == (0, "ok", [])


def test_issue_3955_single_named_rerun_does_not_count_the_stand_in(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """同じ head の打ち直し (c): 名指し 1 者（kiro）なら、前回の claude を kiro の代わりに数えない。"""
    state_dir = tmp_path / "st"
    _converged_with_kiro_filled(capsys, monkeypatch, wt, state_dir)
    again = Fake(state_dir, {"kiro": [[]]})
    _run_seats(capsys, monkeypatch, again, wt, "--reviewer", "kiro")
    assert again.calls == ["kiro"]


def test_issue_3955_allowed_rerun_after_an_allowed_fill_is_reused(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """同じ head の打ち直し (b): allowed が claude と codex で収束した後の打ち直しは前回の結果を返し、起動の前に埋めたことが載る。"""
    _allow(wt, "claude", "codex")
    state_dir = tmp_path / "st"
    _run_seats(capsys, monkeypatch, Fake(state_dir, {}), wt)
    again = Fake(state_dir, {})
    code, got = _run_seats(capsys, monkeypatch, again, wt)
    assert (code, got["status"], again.calls) == (0, "ok", [])
    assert any(w.startswith("前回の round 1") and "allowed の相手役 1（codex）" in w for w in got["warnings"])


def test_issue_3955_one_previous_seat_does_not_stand_in_for_two(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """同じ head の打ち直し (d): 前回が kiro と、codex の席を埋めた claude なら、allowed を claude と codex にした打ち直しは
    前回の claude を codex と claude の両方に数えず、レビューを回す。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [FAIL, FAIL], "kiro": [[]], "claude": [[]]}), wt)
    assert _state(state_dir)["rounds"][-1]["reviewers"] == ["claude", "kiro"]
    _allow(wt, "claude", "codex")
    again = Fake(state_dir, {})
    _run_seats(capsys, monkeypatch, again, wt)
    assert sorted(again.calls) == ["claude", "codex"]


def _guarded(fake: Fake, monkeypatch, claude_version: Optional[str]) -> Fake:
    """起動の前の下限の検査（`agents._guarded_run`）を通す起動。子のプロセスの代わりに偽物を呼び、claude のバージョンを差し替える。"""
    from ndf.adapters import cli_floor

    monkeypatch.setattr(cli_floor, "resolve", lambda argv0, env=None, cwd=None: f"/bin/{argv0}")
    versions = {"claude": claude_version, "codex": "0.200.0"}
    monkeypatch.setattr(cli_floor, "_version", lambda name, path, row, env, cwd: versions.get(name))
    monkeypatch.setattr(agents, "_start_and_watch", lambda spec, poll: fake(spec))
    monkeypatch.setattr(agents, "kiro_guard", lambda cwd, paths: __import__("contextlib").nullcontext({}))
    monkeypatch.setattr(agents, "record_cost_file", lambda out: None)

    class Guarded(Fake):
        def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
            return agents._guarded_run(spec, None)

    guarded = Guarded(fake.state_dir, {})
    return guarded


@pytest.mark.parametrize("version", ["2.1.315", None])
def test_issue_3955_old_or_unknown_claude_refuses_only_the_seat(wt: Path, tmp_path: Path, capsys, monkeypatch, version) -> None:
    """claude の下限 (a)(b)(e): 2.1.315 かバージョンを読めないとき、席を埋めない実行はレビューと修正担当まで今と同じに進み、kiro の席を
    埋める実行では席の claude だけが起動されずに断られ、結果の無い席として incomplete_reviewers で止まる。"""
    plain = Fake(tmp_path / "st", {"codex": [["直す"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, _guarded(plain, monkeypatch, version), wt)
    assert (code, got["status"]) == (0, "ok") and plain.calls.count("claude") == 1
    seated = Fake(tmp_path / "st2", {"codex": [[]], "kiro": [FAIL, FAIL], "claude": [[]]})
    code, got = _run(capsys, monkeypatch, _guarded(seated, monkeypatch, version), wt)
    assert fin.INCOMPLETE_REVIEWERS in got["summary"] and seated.seat_calls == []
    assert any("相手役 claude が差分を読めなかった" in w and "refused" in w for w in got["warnings"])


class _Denied(Fake):
    """席の claude の出力の外側の JSON に Read の拒否を足す偽物。`target` はリクエストで渡した差分のファイルのパスと claude の
    作業ディレクトリから、拒否された引数のパスを作る。"""

    def __init__(self, state_dir: Path, script: dict, target: Callable[[str, Path], str]):
        super().__init__(state_dir, script)
        self.target = target

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        status = super().__call__(spec)
        if spec.agent == "claude" and "--restricted" in spec.argv:
            found = re.search(r"差分は (\S+?)（", spec.argv[-1])
            assert found is not None
            body = json.loads(spec.paths.out.read_text(encoding="utf-8"))
            body["permission_denials"] = [
                {"tool_name": "Read", "tool_use_id": "t1", "tool_input": {"file_path": self.target(found.group(1), spec.cwd)}}
            ]
            spec.paths.out.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return status


@pytest.mark.parametrize("target", [lambda diff, cwd: diff, lambda diff, cwd: os.path.relpath(diff, cwd)])
def test_pr_4461_seat_claude_denied_the_diff_is_not_an_answer(wt: Path, tmp_path: Path, capsys, monkeypatch, target) -> None:
    """PR 4461 の r1-1・r2-1・r2-3: 埋めた claude が渡した差分のファイルの Read を拒否されたら（引数が作業ディレクトリからの相対パスでも）、
    `{"findings": []}` で完了しても結果が無い席と数え、収束にせず incomplete_reviewers で止まる。PR に載る注意には差分のファイルの
    名前だけを書き、絶対パスは過程にだけ残す。拒否が差分のファイルでなければ回答として扱う。"""
    script = {"codex": [[]], "kiro": [FAIL, FAIL], "claude": [[]]}
    fake = _Denied(tmp_path / "st", script, target)
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert fin.INCOMPLETE_REVIEWERS in got["summary"] and fake.seat_calls == ["claude"]
    # 理由は席ごとの注意に、直し方はラウンド全体の注意（結果の無い席ごとの行）に載る
    denied = f"{drive.DENIED_READ}: round1.diff"
    assert any("相手役 claude が差分を読めなかった" in w and denied in w for w in got["warnings"])
    assert any(f"- claude: {denied}。{drive.DENIED_FIX}" in w for w in got["warnings"])
    assert fake.input_dir is not None and not any(str(fake.input_dir) in w for w in got["warnings"])
    assert any(str(fake.input_dir / "round1.diff") in p["result"] for p in got["process"])
    other = _Denied(tmp_path / "st2", script, lambda diff, cwd: "/etc/passwd")
    code, got = _run(capsys, monkeypatch, other, wt)
    assert (code, got["status"]) == (0, "ok")
