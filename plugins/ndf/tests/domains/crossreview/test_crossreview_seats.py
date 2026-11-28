"""#3955: cross-review の空いた席を claude の読むだけの起動で埋める（相手役と修正担当は偽物、git は一時の origin で本物を打つ）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import pytest

from ndf.adapters import agents, git
from ndf.contracts import result
from ndf.domains.crossreview import cli, drive, seats
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, run_cli
from ._judge import is_judge
from .test_crossreview_run import FAIL, FAILED_WITH_FINDINGS, NO_FINDINGS, PR, Fake, _state, seat_of

# 相手役に codex と kiro を名指す起動（`test_crossreview_run._run` と同じ）
NAMED = ("--reviewer", "codex", "--reviewer", "kiro")


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


def _refuse(monkeypatch, refused: dict[str, str]) -> None:
    from ndf.core.converge import participants

    monkeypatch.setattr(participants, "refused_runtimes", lambda runtimes: {r: w for r, w in refused.items() if r in runtimes})


def _prompts(fake: Fake, out: str) -> list[str]:
    return [s.argv[-1] for s in fake.launches if s.paths.out.name == out]


class _Guarded(Fake):
    """起動の前の下限の検査（`agents._guarded_run`）を通す偽物。検査を通った起動だけが台本どおりに動き、`calls` に載る。"""

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        return agents._guarded_run(spec, None)


def _guarded(claude_version: Optional[str]) -> Callable[..., Fake]:
    """claude のバージョンを差し替え、子のプロセスの代わりに偽物を呼ぶ `_Guarded` を作る。"""

    def make(state_dir: Path, script: dict, monkeypatch) -> Fake:
        from ndf.adapters import cli_floor

        fake = _Guarded(state_dir, script)
        monkeypatch.setattr(cli_floor, "resolve", lambda argv0, env=None, cwd=None: f"/bin/{argv0}")
        versions = {"claude": claude_version, "codex": "0.200.0"}
        monkeypatch.setattr(cli_floor, "_version", lambda name, path, row, env, cwd: versions.get(name))
        monkeypatch.setattr(agents, "_start_and_watch", lambda spec, poll: Fake.__call__(fake, spec))
        monkeypatch.setattr(agents, "kiro_guard", lambda cwd, paths: __import__("contextlib").nullcontext({}))
        monkeypatch.setattr(agents, "record_cost_file", lambda out: None)
        return fake

    return make


class _Denied(Fake):
    """席の claude の出力の外側の JSON に Read の拒否を足す偽物。`target` はリクエストで渡した差分のファイルのパスと claude の
    作業ディレクトリから、拒否された引数のパスを作る。"""

    def __init__(self, state_dir: Path, script: dict, target: Callable[[str, Path], str]):
        super().__init__(state_dir, script)
        self.target = target

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        status = super().__call__(spec)
        if spec.agent == "claude" and "--restricted" in spec.argv and not is_judge(spec):
            found = re.search(r"差分は (\S+?)（", spec.argv[-1])
            assert found is not None
            body = json.loads(spec.paths.out.read_text(encoding="utf-8"))
            body["permission_denials"] = [
                {"tool_name": "Read", "tool_use_id": "t1", "tool_input": {"file_path": self.target(found.group(1), spec.cwd)}}
            ]
            spec.paths.out.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return status


def _denied(target: Callable[[str, Path], str]) -> Callable[..., Fake]:
    return lambda state_dir, script, monkeypatch: _Denied(state_dir, script, target)


# ---------------------------------------------------------------- 1 回の起動の場面（席・起動・記録・注意）


class Case(NamedTuple):
    """1 回の起動の場面。`want` は `ok`・`precondition`・`fin.INCOMPLETE_REVIEWERS`。`seat_calls` は席の claude の起動の
    席の名前（並べて起動するので順は問わない）。`reviewers`・`filled` は round 1 の記録（None は見ない）。`check` は場面ごとの
    残りの確かめ（偽物・結果・PR のコメントの偽物を受け取る）。`make` は偽物を作る（既定は `Fake`）。"""

    script: dict
    want: str
    seat_calls: tuple[str, ...] = ()
    extra: tuple[str, ...] = NAMED
    allowed: Optional[tuple[str, ...]] = None
    refused: Optional[dict[str, str]] = None
    reviewers: Optional[list[str]] = None
    filled: Optional[list[dict]] = None
    check: Optional[Callable] = None
    make: Optional[Callable[..., Fake]] = None


def _before_launch(fake: Fake, got: dict, hub) -> None:
    assert sorted(fake.calls) == ["claude", "codex"]
    assert any("起動の前" in w and "allowed の相手役 1（codex）" in w for w in got["warnings"])


def _claude_only(fake: Fake, got: dict, hub) -> None:
    assert set(fake.calls) == {"claude"}


def _two_claude_outputs(fake: Fake, got: dict, hub) -> None:
    _claude_only(fake, got, hub)
    assert sorted(s.paths.out.name for s in _seat_specs(fake)) == ["round1-claude-2.out", "round1-claude.out"]


def _kiro_not_launched(fake: Fake, got: dict, hub) -> None:
    assert "kiro" not in fake.calls


def _nothing_launched(fake: Fake, got: dict, hub) -> None:
    assert "担当を起動できない" in got["cause"]["detail"] and fake.calls == []


def _no_kiro_in_round_two(fake: Fake, got: dict, hub) -> None:
    assert "kiro" not in [seat_of(s) for s in fake.launches if "round2-" in s.paths.out.name]


def _kiro_twice(fake: Fake, got: dict, hub) -> None:
    assert fake.calls.count("kiro") == 2


def _only_two_reviewers(fake: Fake, got: dict, hub) -> None:
    assert sorted(fake.calls) == ["codex", "kiro"]
    assert all("filled" not in r for r in _state(fake.state_dir)["rounds"])


def _filled_in_round_one(fake: Fake, got: dict, hub) -> None:
    assert _state(fake.state_dir)["rounds"][0]["filled"][-1] == {"seat": "claude-2", "for": "codex", "reason": "failed: 落ちた"}
    assert "round 1に相手役 codex の席を claude の読むだけの起動（claude-2）で埋めた（理由: failed: 落ちた）" in got["warnings"]


def _kiro_seat_for_the_rest(fake: Fake, got: dict, hub) -> None:
    """2 ラウンド目は kiro を起動せず、両者に前の head からの差分を渡す。注意・記録・コメントに埋めた席が載る。"""
    _kiro_twice(fake, got, hub)
    assert any("相手役 kiro の席" in w and "failed: 落ちた" in w and "（claude）" in w and w.startswith("round 1") for w in got["warnings"])
    rounds = _state(fake.state_dir)["rounds"]
    filled = [{"seat": "claude", "for": "kiro", "reason": "failed: 落ちた"}]
    assert [(r["reviewers"], r["filled"], r["scope"]) for r in rounds] == [
        (["codex", "claude"], filled, "full"),
        (["codex", "claude"], filled, "since"),
    ]
    assert {f["by"] for f in _state(fake.state_dir)["findings"]} == {"codex", "claude"}
    since = _prompts(fake, "round2-codex.out") + _prompts(fake, "round2-claude.out")
    assert len(since) == 2 and all(f"{fake.input_dir}/round2.diff" in p and "since" in p for p in since)
    assert "| codex・claude（kiro の席） |" in hub.comments[-1][2]


def _whole_diff_in_round_two(fake: Fake, got: dict, hub) -> None:
    """round 2 で初めて埋めた claude には全差分と前回の情報を含まないリクエストの文、round 3 は両者に round 2 の head からの差分。"""
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


def _one_claude_launch(fake: Fake, got: dict, hub) -> None:
    assert fake.calls.count("claude") == 1


def _seat_refused(fake: Fake, got: dict, hub) -> None:
    assert any("相手役 claude が差分を読めなかった" in w and "refused" in w for w in got["warnings"])


def _denied_diff(fake: Fake, got: dict, hub) -> None:
    """PR に載る注意には差分のファイルの名前だけを書き、絶対パスは過程にだけ残す。理由は席ごとの注意に、直し方はラウンド全体の注意に載る。"""
    denied = f"{seats.DENIED_READ}: round1.diff"
    assert any("相手役 claude が差分を読めなかった" in w and denied in w for w in got["warnings"])
    assert any(f"- claude: {denied}。{seats.DENIED_FIX}" in w for w in got["warnings"])
    assert fake.input_dir is not None and not any(str(fake.input_dir) in w for w in got["warnings"])
    assert any(str(fake.input_dir / "round1.diff") in p["result"] for p in got["process"])


KIRO_FAILS = {"codex": [[]], "kiro": [FAIL, FAIL], "claude": [[]]}
BEFORE_1 = [{"seat": "claude", "for": None, "reason": "allowed の相手役 1（codex）"}]
CLAUDE_CODEX = ("claude", "codex")
PRECONDITION = {"want": "precondition", "check": _nothing_launched, "extra": ()}


@pytest.mark.parametrize(
    "case",
    [
        # 受け入れ条件 5: allowed が claude と codex なら codex と claude の 2 者で、起動の前に埋めたことが残る
        Case({"codex": [[]], "claude": [[]]}, "ok", ("claude",), (), CLAUDE_CODEX, None, ["codex", "claude"], BEFORE_1, _before_launch),
        # 受け入れ条件 6・決定 3: allowed が claude だけなら claude と claude-2 で、codex と kiro を起動しない。出力は round<n>-claude-2
        Case({"claude": [[]], "claude-2": [[]]}, "ok", ("claude", "claude-2"), (), ("claude",), None, ["claude", "claude-2"], None, _two_claude_outputs),
        # 受け入れ条件 15 (a)(b): 始める前の確かめで断った相手役を起動せず、claude（・claude-2）で埋める
        Case(
            {"codex": [[]], "claude": [[]]}, "ok", ("claude",), (), None, {"kiro": "学習に使う設定"}, ["codex", "claude"],
            [{"seat": "claude", "for": "kiro", "reason": "unready: 学習に使う設定"}], _kiro_not_launched,
        ),
        Case({}, "ok", ("claude", "claude-2"), (), None, {"kiro": "k", "codex": "c"}, ["claude", "claude-2"], None, _claude_only),
        # 受け入れ条件 15 (c)(d)・名指しの数え方: 修正担当か、1 者だけ名指した相手役が断られると、どの担当も起動せずに止まる
        Case({}, refused={"claude": "c"}, **PRECONDITION),
        Case({}, refused={"kiro": "k"}, **{**PRECONDITION, "extra": ("--reviewer", "kiro")}),
        Case({}, refused={"codex": "c"}, **{**PRECONDITION, "extra": ("--reviewer", "codex", "--reviewer", "codex")}),
        # 受け入れ条件 9: 相手役が 2 者とも読めれば claude を相手役として起動せず、ラウンドの記録に filled が無い
        Case({"codex": [[]], "kiro": [[]]}, "ok", reviewers=["codex", "kiro"], check=_only_two_reviewers),
        # 受け入れ条件 1〜4: kiro が起動し直しても読めないと、残りの実行は claude が kiro の席でレビューする
        Case({"codex": [["直す"], []], "kiro": [FAIL, FAIL], "claude": [["claude の指摘"], []]}, "ok", ("claude", "claude"), check=_kiro_seat_for_the_rest),
        # round 2 で初めて埋めた claude は --base からの全差分を読む
        Case(
            {"codex": [["一つ目"], ["二つ目"], []], "kiro": [[], FAIL, FAIL], "claude": [[], []]}, "ok", ("claude", "claude"),
            (*NAMED, "--max-rounds", "3"), check=_whole_diff_in_round_two,
        ),
        # 起動し直しの回数 (a)(b): 席の claude は起動し直さない
        Case({"claude": [[]], "claude-2": [FAIL]}, fin.INCOMPLETE_REVIEWERS, ("claude", "claude-2"), (), ("claude",)),
        Case({"codex": [["直す"], []], "kiro": [FAIL, FAIL], "claude": [[], FAIL]}, fin.INCOMPLETE_REVIEWERS, ("claude", "claude"), check=_no_kiro_in_round_two),
        # 分岐の順 (a)〜(c): 席が codex と claude のとき、両方が読めなければ precondition、claude の席だけなら埋めずに
        # incomplete_reviewers、codex だけなら codex の位置を claude-2 で埋め、記録と「round 1 に」の注意が残る（I-001 の前の固定）
        Case({"codex": [FAIL, FAIL], "claude": [FAIL]}, "precondition", ("claude",), (), CLAUDE_CODEX),
        Case({"codex": [[]], "claude": [FAIL]}, fin.INCOMPLETE_REVIEWERS, ("claude",), (), CLAUDE_CODEX),
        Case(
            {"codex": [FAIL, FAIL], "claude": [[]], "claude-2": [[]]}, "ok", ("claude", "claude-2"), (), CLAUDE_CODEX, None,
            ["claude-2", "claude"], None, _filled_in_round_one,
        ),
        # 受け入れ条件 12・名指しの数え方: 1 者だけ名指して読めなければ precondition で、claude を相手役として起動しない
        Case({"codex": [FAIL, FAIL]}, "precondition", extra=("--reviewer", "codex")),
        Case({"codex": [FAIL, FAIL]}, "precondition", extra=("--reviewer", "codex", "--reviewer", "codex")),
        Case({"codex": [[]]}, "ok", extra=("--reviewer", "codex"), reviewers=["codex"]),
        Case({"codex": [[]]}, "ok", extra=("--reviewer", "codex", "--reviewer", "codex"), reviewers=["codex"]),
        # 結果の有無: 指摘の配列が無い席と、完了でない状態で終わった席は、有効な出力があっても結果が無い（起動し直し・埋める）
        Case({"codex": [[]], "kiro": [NO_FINDINGS, NO_FINDINGS], "claude": [[]]}, "ok", ("claude",), check=_kiro_twice),
        Case({"codex": [[]], "kiro": [FAILED_WITH_FINDINGS] * 2, "claude": [[]]}, "ok", ("claude",), check=_kiro_twice),
        # claude の下限 (a)(b)(e): 2.1.315 かバージョンを読めないとき、席を埋めない実行は今と同じに進み、kiro の席を埋める
        # 実行では席の claude だけが起動されずに断られ、結果の無い席として incomplete_reviewers で止まる
        *(
            c
            for version in ("2.1.315", None)
            for c in (
                Case({"codex": [["直す"], []], "kiro": [[], []]}, "ok", check=_one_claude_launch, make=_guarded(version)),
                Case(KIRO_FAILS, fin.INCOMPLETE_REVIEWERS, check=_seat_refused, make=_guarded(version)),
            )
        ),
        # PR 4461 の r1-1・r2-1・r2-3: 埋めた claude が渡した差分のファイルの Read を拒否されたら（相対パスでも）、
        # `{"findings": []}` で完了しても結果が無い席と数える。拒否が差分のファイルでなければ回答として扱う
        Case(KIRO_FAILS, fin.INCOMPLETE_REVIEWERS, ("claude",), check=_denied_diff, make=_denied(lambda diff, cwd: diff)),
        Case(KIRO_FAILS, fin.INCOMPLETE_REVIEWERS, ("claude",), check=_denied_diff, make=_denied(lambda diff, cwd: os.path.relpath(diff, cwd))),
        Case(KIRO_FAILS, "ok", ("claude",), make=_denied(lambda diff, cwd: "/etc/passwd")),
    ],
)  # fmt: skip
def test_issue_3955_seat_scenarios(wt: Path, tmp_path: Path, capsys, monkeypatch, hub, case: Case) -> None:
    """1 回の起動で、席の決め方・埋め方・起動し直し・記録・注意が場面ごとに決まった形になる。"""
    if case.allowed is not None:
        _allow(wt, *case.allowed)
    if case.refused is not None:
        _refuse(monkeypatch, case.refused)
    make = case.make or (lambda state_dir, script, mp: Fake(state_dir, script))
    fake = make(tmp_path / "st", case.script, monkeypatch)
    code, got = _run_seats(capsys, monkeypatch, fake, wt, *case.extra)
    if case.want == "ok":
        assert (code, got["status"]) == (0, "ok")
    elif case.want == "precondition":
        assert got["cause"]["kind"] == "precondition"
    else:
        assert case.want in got["summary"]
    assert sorted(fake.seat_calls) == sorted(case.seat_calls)
    if case.reviewers is not None:
        assert _state(fake.state_dir)["rounds"][0]["reviewers"] == case.reviewers
    if case.filled is not None:
        assert _state(fake.state_dir)["rounds"][0]["filled"] == case.filled
    if case.check is not None:
        case.check(fake, got, hub)


# ---------------------------------------------------------------- 同じ head の打ち直し


class Again(NamedTuple):
    """打ち直し 1 回。`calls` は起動された担当（順は問わない）。空なら前回の結果を返す（ok）。`warning` は 1 つの注意に載る語。"""

    allowed: Optional[tuple[str, ...]]
    refused: Optional[dict[str, str]]
    extra: tuple[str, ...]
    script: dict
    calls: tuple[str, ...] = ()
    warning: tuple[str, ...] = ()


@pytest.mark.parametrize(
    ("first", "again"),
    [
        # (a)(e)・始める前の確かめ (f): 前回埋めた席を欠けた相手役の代わりにも数え、注意に kiro の席を claude で埋めたことと
        # 理由が載る。kiro を断る設定でも確かめずに返し、allowed を claude と codex に絞っても返す
        (
            (None, NAMED, KIRO_FAILS, ["codex", "claude"]),
            [
                Again(None, {"kiro": "k"}, NAMED, {}, (), ("前回の round 1", "相手役 kiro の席", "failed: 落ちた")),
                Again(CLAUDE_CODEX, {"kiro": "k"}, (), {}),
            ],
        ),
        # (c): 名指し 1 者（kiro）なら、前回の claude を kiro の代わりに数えない
        ((None, NAMED, KIRO_FAILS, ["codex", "claude"]), [Again(None, None, ("--reviewer", "kiro"), {"kiro": [[]]}, ("kiro",))]),
        # (b): allowed が claude と codex で収束した後の打ち直しは前回の結果を返し、起動の前に埋めたことが載る
        ((CLAUDE_CODEX, (), {}, ["codex", "claude"]), [Again(None, None, (), {}, (), ("前回の round 1", "allowed の相手役 1（codex）"))]),
        # (d): 前回が kiro と codex の席を埋めた claude なら、allowed を claude と codex にした打ち直しは前回の claude を両方に数えない
        (
            (None, NAMED, {"codex": [FAIL, FAIL], "kiro": [[]], "claude": [[]]}, ["claude", "kiro"]),
            [Again(CLAUDE_CODEX, None, (), {}, ("claude", "codex"))],
        ),
    ],
)
def test_issue_3955_same_head_rerun(wt: Path, tmp_path: Path, capsys, monkeypatch, first, again: list[Again]) -> None:
    allowed, extra, script, reviewers = first
    state_dir = tmp_path / "st"
    if allowed is not None:
        _allow(wt, *allowed)
    _run_seats(capsys, monkeypatch, Fake(state_dir, script), wt, *extra)
    assert _state(state_dir)["rounds"][-1]["reviewers"] == reviewers
    for step in again:
        if step.allowed is not None:
            _allow(wt, *step.allowed)
        if step.refused is not None:
            _refuse(monkeypatch, step.refused)
        fake = Fake(state_dir, step.script)
        code, got = _run_seats(capsys, monkeypatch, fake, wt, *step.extra)
        assert sorted(fake.calls) == sorted(step.calls)
        if not step.calls:
            assert (code, got["status"]) == (0, "ok") and _state(state_dir)["rounds"][-1]["verdict"] == fin.CONVERGED
        if step.warning:
            assert any(all(word in w for word in step.warning) and w.startswith(step.warning[0]) for w in got["warnings"])


# ---------------------------------------------------------------- 資料の保存先・review-scope・学習の設定の確かめ


def test_issue_3955_seat_claude_reads_only_this_runs_inputs(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """受け入れ条件 7・8: 席の claude は最小構成の読むだけの起動で、修正担当の会話を持たない。--add-dir は実行ごとに新しく作った
    review-input-* だけを指し、リクエストの文が指す資料はすべてその下にある。前の実行の資料とリンクは使わず、実行の終わりに消える。
    止まった実行でも資料は消える。"""
    stopped = Fake(tmp_path / "st2", {"codex": [FAIL, FAIL], "kiro": [FAIL, FAIL]})
    code, got = _run_seats(capsys, monkeypatch, stopped, wt, *NAMED)
    assert got["cause"]["kind"] == "precondition"
    assert list(stopped.state_dir.glob(f"{drive.INPUT_PREFIX}*")) == []
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


def test_issue_3955_claude_only_scope_and_training_check(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """allowed が claude だけのとき: review-scope も run と同じ席で範囲を決めて invalid で止まらず、`--reviewer claude` は invalid。
    受け入れ条件 15 (e): 席が claude と claude-2 でも、学習の設定は claude について 1 回だけ確かめる。"""
    from ndf.adapters import training

    _allow(wt, "claude")
    argv = ["review-scope", str(wt), "--state-dir", str(tmp_path / "st"), "--pr", str(PR), "--purpose", "design", "--base", "origin/main"]
    code, got = run_cli(capsys, *argv)
    assert (code, got["status"]) == (0, "ok")
    run_argv = ["run", "--pr", "1", "--state-dir", str(tmp_path / "st"), "--base", "origin/main", "--worktree", str(wt)]
    assert cli._run_config(cli._parser().parse_args(run_argv)).reviewers == ("claude", "claude-2")
    code, got = run_cli(capsys, *argv, "--reviewer", "claude")
    assert got["cause"]["kind"] == "invalid"
    asked: list[str] = []
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: asked.append(runtime))
    _run_seats(capsys, monkeypatch, Fake(tmp_path / "st2", {}), wt)
    assert asked == ["claude"]


class _SeatRun:
    """`seats.fill_refused` が読み書きする値だけを持つ起動（`SeatRun`）。"""

    def __init__(self, seats_: list[str], fixer: str = "claude", may_fill: bool = True):
        from types import SimpleNamespace

        self.cfg = SimpleNamespace(fixer=fixer, may_fill=may_fill)
        self.seats = seats_
        self.filled: list = []
        self.warnings: list[str] = []


@pytest.mark.parametrize(
    ("before", "refused", "after", "filled", "fixer", "may_fill"),
    [
        (["codex", "kiro"], {"kiro": "k"}, ["codex", "claude"], [("claude", "kiro", "unready: k")], "claude", True),
        (["kiro", "codex"], {"kiro": "k"}, ["claude", "codex"], [("claude", "kiro", "unready: k")], "claude", True),
        (
            ["codex", "kiro"],
            {"kiro": "k", "codex": "c"},
            ["claude", "claude-2"],
            [("claude", "codex", "unready: c"), ("claude-2", "kiro", "unready: k")],
            "claude",
            True,
        ),
        (["codex", "claude"], {"codex": "c"}, ["claude-2", "claude"], [("claude-2", "codex", "unready: c")], "claude", True),
        (["codex", "kiro"], {}, ["codex", "kiro"], [], "claude", True),
        # 修正担当が断られたか、埋めてはいけない起動で断られたら止まる（`after` は None）
        (["codex", "kiro"], {"claude": "c"}, None, [], "claude", True),
        (["kiro"], {"kiro": "k"}, None, [], "claude", False),
    ],
)
def test_i001_fill_refused(monkeypatch, before, refused, after, filled, fixer, may_fill) -> None:
    """I-001 の前の固定: 断られた席を同じ位置で、その時点の席に無い最初の claude の名前（claude・claude-2…）に置き換え、席の順に
    埋めた席（`unready: <理由>`）と「起動の前に」の注意を残す。断られた席が無ければ何も変えない。修正担当が断られたか、埋めては
    いけない起動で断られたら、席と記録を変えずに precondition で止める。"""
    from ndf.domains.crossreview.scope_decide import Filled

    _refuse(monkeypatch, refused)
    run = _SeatRun(list(before), fixer=fixer, may_fill=may_fill)
    if after is None:
        with pytest.raises(seats.DriveStopped) as stopped:
            seats.fill_refused(run)
        assert stopped.value.kind == "precondition" and "担当を起動できない: " in str(stopped.value)
        assert (run.seats, run.filled, run.warnings) == (before, [], [])
        return
    seats.fill_refused(run)
    assert run.seats == after
    assert run.filled == [Filled(*f) for f in filled]
    assert run.warnings == [
        f"起動の前に相手役 {absent} の席を claude の読むだけの起動（{seat}）で埋めた（理由: {reason}）" for seat, absent, reason in filled
    ]


class _LaunchRun:
    """`seats.launch_reviewers` が読み書きする値だけを持つ起動（`SeatRun`）。`notes` は `note` の引数の並び。"""

    def __init__(self, root: Path):
        from types import SimpleNamespace

        self.cfg = SimpleNamespace(worktree=root)
        self.input_dir: Optional[Path] = None
        self.root = root
        self.notes: list[tuple[int, str, str, Optional[str]]] = []

    def round_stem(self, round_no: int, suffix: str) -> Path:
        return self.root / f"round{round_no}{suffix}"

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None:
        self.notes.append((round_no, who, result, warning))


def _seat_launcher(
    outcomes: dict[str, tuple[str, str, str, str]],
) -> Callable[[agents.Launch], agents.AgentStatus]:
    """席の出力の名前（`round<n>-<席>`）ごとに (状態, 詳細, 標準出力, 標準エラー) を書いて返す偽の起動。空の文字列は書かない。"""

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        seat = next(n for n in outcomes if spec.paths.out.name.removesuffix(".out").split("-", 1)[1] in (n, f"{n}-retry"))
        state, detail, out, err = outcomes[seat]
        if out:
            spec.paths.out.write_text(out, encoding="utf-8")
        if err:
            spec.paths.err.write_text(err, encoding="utf-8")
        return agents.AgentStatus(spec.agent, state, 0, detail, ())

    return launch


def _claude_out(findings: list, denied: Optional[Path] = None) -> str:
    body: dict = {"result": json.dumps({"findings": findings})}
    if denied is not None:
        body["permission_denials"] = [{"tool_name": "Read", "tool_use_id": "t1", "tool_input": {"file_path": str(denied)}}]
    return json.dumps(body)


@pytest.mark.parametrize("suffix", ["", "-retry"])
def test_i004_launch_reviewers_kind_and_cause_per_branch(tmp_path: Path, suffix: str) -> None:
    """I-004 の前の固定: 結果の無い席の過程の種類（kind）と理由（cause）を決める 3 分岐。
    (1) 標準エラーにサンドボックスの失敗があれば、完了して指摘があっても kind は `sandbox`・cause はその行。
    (2) claude の席が渡した差分のファイルの読み取りを拒否されたら、kind は `permission_denials: <絶対パス>`・cause は
    `permission_denials: <ファイル名>`。(3) それ以外は kind が状態・cause が `<状態>: <詳細>`。サンドボックスは拒否より先に見る。
    指摘を返した席は結果の無い席に入らず、指摘に席の名前が付く。"""
    diff = tmp_path / "in" / "round1.diff"
    other = tmp_path / "in" / "round1-full.diff"
    sandbox_line = f"{agents.SANDBOX_FAILED} No permissions to create a new namespace"
    done, failed = str(agents.AgentState.DONE), str(agents.AgentState.FAILED)
    outcomes = {
        "codex": (done, "", '{"findings": [{"title": "x"}]}', f"前置き\n{sandbox_line}\n後ろ"),
        "claude": (done, "", _claude_out([], diff), ""),
        "claude-2": (done, "", _claude_out([], diff), f"{sandbox_line}\n"),
        "kiro": (failed, "落ちた", "", ""),
        "claude-3": (done, "", _claude_out([{"title": "y"}]), ""),
    }
    run = _LaunchRun(tmp_path)
    prompts = dict.fromkeys(outcomes, "見て")
    raised, unread = seats.launch_reviewers(run, 1, prompts, [diff, other], _seat_launcher(outcomes), suffix)
    again = "（起動し直した）" if suffix else ""
    want = {
        "codex": ("sandbox", sandbox_line),
        "claude": (f"{seats.DENIED_READ}: {diff}", f"{seats.DENIED_READ}: round1.diff"),
        "claude-2": ("sandbox", sandbox_line),
        "kiro": ("failed", "failed: 落ちた"),
    }
    assert raised == [{"title": "y", "by": "claude-3"}]
    assert run.notes == [
        *(
            (1, seat, f"回答無し{again}（{kind}）", f"round 1 の相手役 {seat} が差分を読めなかった{again}（{cause}）")
            for seat, (kind, cause) in want.items()
        ),
        (1, "claude-3", f"指摘 1 件{again}", None),
    ]
    assert list(unread) == list(want)
    for seat, (_, cause) in want.items():
        err = tmp_path / f"round1-{seat}{suffix}.err.log"
        hint, local = seats._unread_hint(seat, cause, err)
        assert unread[seat] == seats.Unread(hint, cause, local)
    assert f"- claude: {seats.DENIED_READ}: round1.diff。{seats.DENIED_FIX}" in unread["claude"].hint
    assert unread["codex"].hint.startswith(f"- codex: {sandbox_line}。サンドボックスが名前空間を作れない")
    assert unread["kiro"].hint.startswith("- kiro: failed: 落ちた。ログインを `")
    assert unread["kiro"].hint.endswith(f"（標準エラー: round1-kiro{suffix}.err.log）")
    assert unread["kiro"].local.endswith(f"（標準エラー: {tmp_path / f'round1-kiro{suffix}.err.log'}）")

    # 拒否された差分のファイルが 2 つなら、`required` の順に cause は名前を、kind は絶対パスを ` / ` でつなぐ。
    # 指摘が無い完了でない席（出力無し）は状態と詳細を cause にする
    first, second = tmp_path / "in" / "a.diff", tmp_path / "in" / "b.diff"
    body = {
        "result": json.dumps({"findings": []}),
        "permission_denials": [
            {"tool_name": "Read", "tool_use_id": "t2", "tool_input": {"file_path": str(second)}},
            {"tool_name": "Read", "tool_use_id": "t1", "tool_input": {"file_path": str(first)}},
        ],
    }
    stalled = str(agents.AgentState.STALLED)
    outcomes = {"claude": (done, "", json.dumps(body), ""), "codex": (stalled, "止まった", "", "")}
    run = _LaunchRun(tmp_path)
    raised, unread = seats.launch_reviewers(run, 2, dict.fromkeys(outcomes, "見て"), [first, second], _seat_launcher(outcomes), suffix)
    assert raised == []
    assert [n[2] for n in run.notes] == [f"回答無し{again}（{seats.DENIED_READ}: {first} / {second}）", f"回答無し{again}（stalled）"]
    assert unread["claude"].cause == f"{seats.DENIED_READ}: a.diff / b.diff"
    assert unread["codex"].cause == "stalled: 止まった"
