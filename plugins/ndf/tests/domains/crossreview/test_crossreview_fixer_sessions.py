"""cross-review の修正担当の会話の指定（#4013）。`.ndf/review.json` の `fixer_sessions` が真のリポジトリだけ、2 ラウンド目からの
修正担当が前のラウンドの会話を続ける。相手役と修正担当は偽物に差し替え、git は一時の origin で本物を打つ（`test_crossreview_run` の形）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import agents, claude, git
from ndf.core.converge import participants
from ndf.domains.crossreview import cli, criteria_decide, drive, fixer, resume_decide, settings, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records.mvv_decide import REVIEW_FIXED_AT

from ._repos import BRANCH, Repos, run_cli
from .test_crossreview_run import PR, Fake, _run, _state

# --- #4013: 修正担当の会話の指定 ---------------------------------------------------------------

# 会話の指定の引数（会話を保存しない起動には現れない）
SESSION_FLAGS = ("--session-id", "--resume")
# 修正担当が 2 回起動される台本（1・2 ラウンド目に指摘が 1 件ずつ、3 ラウンド目に 0 件で収束）
TWO_FIXES = {"codex": [["一つ目"], ["二つ目"], []], "kiro": [[], [], []]}
# 修正担当が 3 回起動される台本（1〜3 ラウンド目に指摘が 1 件ずつ、4 ラウンド目に 0 件で収束）
THREE_FIXES = {"codex": [["一つ目"], ["二つ目"], ["三つ目"], []], "kiro": [[], [], [], []]}


def _fixer_argvs(fake: Fake) -> list[list[str]]:
    return [list(s.argv) for s in fake.launches if s.agent == "claude"]


def _declare(repos: Repos, body: dict) -> None:
    """実行者のメインディレクトリの `.ndf/review.json` を書く。"""
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize("decl", [None, {"version": 1, "focus": []}, {"version": 1, "focus": [], "fixer_sessions": False}])
def test_issue_4013_fixer_without_saved_sessions_never_keeps_a_conversation(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, decl: Optional[dict]
) -> None:
    """#4013 の受け入れ条件 7・4: `fixer_sessions` の無い・偽の設定では、どのラウンドの修正担当も `--no-session-persistence` で
    起動され、会話の指定の引数を持たない。状態ファイルの rounds・findings の形は変わらない。"""
    from ndf.core.converge import participants

    if decl is not None:
        _declare(repos, decl)
    fake = Fake(tmp_path / "st", TWO_FIXES)
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    argvs = _fixer_argvs(fake)
    assert len(argvs) == 2
    for argv in argvs:
        assert "--no-session-persistence" in argv and not set(SESSION_FLAGS) & set(argv)
    state = _state(fake.state_dir)
    assert [f["outcome"] for f in state["findings"]] == [fin.FIXED, fin.FIXED]
    assert all(
        set(f)
        # 直した指摘は直した時刻を持つ（見送りの記録との前後を照らす。#3959）
        == {"by", "id", "key", "outcome", "reason", "round", "section", "text", "severity", "impact", "raised_severity", "occurrence"}
        | {REVIEW_FIXED_AT}
        for f in state["findings"]
    )
    # 相手役の引数は質問のほかは participants が組んだとおり（受け入れ条件 5）
    for spec in fake.launches:
        if spec.agent != "claude":
            built = participants.review_launch(spec.agent, wt, "", tmp_path / "x")
            assert spec.argv[:-1] == built.argv[:-1]


class Sessions(Fake):
    """会話の指定を見る偽の修正担当。`contexts[n]` は n 回目（0 から）の修正担当の結果の行の文脈のトークン数、`window` は
    主モデルの文脈の窓。`compact` の回の出力の途中に自動の要約の行を書く。`missing` の回は会話が見つからない文言を標準
    エラーに出して結果を書かずに落ちる（`plain_fail` の回は文言なしで落ちる）。"""

    def __init__(self, *args: Any, contexts: tuple[int, ...] = (), window: int = 1000000, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.contexts = contexts
        self.window = window
        self.compact: set[int] = set()
        self.missing: set[int] = set()
        self.plain_fail: set[int] = set()
        self._fixer_runs = 0

    def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
        n = self._fixer_runs
        self._fixer_runs += 1
        if n in self.missing or n in self.plain_fail:
            said = f"Error: {claude.NO_CONVERSATION}: {_session_of(list(spec.argv))[1]}\n" if n in self.missing else "落ちた\n"
            spec.paths.err.write_text(said, encoding="utf-8")
            return agents.AgentStatus(spec.agent, agents.AgentState.FAILED, 1, "落ちた", ())
        status = super()._fix(spec)
        context = self.contexts[n] if n < len(self.contexts) else 1000
        iteration = {"input_tokens": 2, "cache_read_input_tokens": context - 2, "cache_creation_input_tokens": 0}
        models = {"claude-opus-5-5": {"inputTokens": context, "outputTokens": 1, "contextWindow": self.window}}
        steps = [{"type": "system", "subtype": "init"}]
        if n in self.compact:
            steps.append({"type": "system", "subtype": "compact_boundary"})
        steps += [{"type": "assistant", "message": {}}, {"type": "result", "usage": {"iterations": [iteration]}, "modelUsage": models}]
        spec.paths.out.write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8")
        return status


def _session_of(argv: list[str]) -> tuple[Optional[str], Optional[str]]:
    """起動の引数の会話の指定（`--session-id`・`--resume` のどちらかと ID）。無ければ (None, None)。"""
    for flag in SESSION_FLAGS:
        if flag in argv:
            return flag, argv[argv.index(flag) + 1]
    return None, None


def _fixer_notes(got: dict) -> list[str]:
    return [p["result"] for p in got["process"] if p["run"].endswith(" claude")]


def _margin() -> int:
    from ndf.contracts import limits

    return int(limits.value(fixer.RESUME_MARGIN, "count"))


SAVED = {"version": 1, "focus": [], "fixer_sessions": True}


def _assert_reviewers_untouched(fake: Fake, wt: Path, tmp_path: Path) -> None:
    """相手役の引数は質問のほかは participants が組んだとおり。"""
    for spec in fake.launches:
        if spec.agent != "claude":
            assert spec.argv[:-1] == participants.review_launch(spec.agent, wt, "", tmp_path / "x").argv[:-1]


def test_issue_4013_second_round_fixer_continues_the_first_conversation(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#4013 の受け入れ条件 1・4・5: `fixer_sessions` が真なら、1 ラウンド目は新しい会話の ID で始め、2 ラウンド目はその ID を
    `--resume` で続ける。会話の ID は状態ファイルに書かず、相手役の引数は変わらない。"""
    _declare(repos, SAVED)
    fake = Sessions(tmp_path / "st", TWO_FIXES)
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    first, second = (_session_of(a) for a in _fixer_argvs(fake))
    assert first[0] == "--session-id" and second == ("--resume", first[1])
    assert all("--no-session-persistence" not in a for a in _fixer_argvs(fake))
    assert _fixer_notes(got) == [f"指摘 1 件を扱った（新しい会話: {resume_decide.FIRST}）", "指摘 1 件を扱った（会話を続けた）"]
    assert first[1] not in (fake.state_dir / store.state_pr(fake.state_dir, PR).path.name).read_text(encoding="utf-8")
    _assert_reviewers_untouched(fake, wt, tmp_path)


@pytest.mark.parametrize(("extra", "resumed"), [(0, True), (-1, False)])
def test_issue_4013_context_that_would_not_fit_the_window_starts_a_new_conversation(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, extra: int, resumed: bool
) -> None:
    """#4013 の受け入れ条件 2: 1 ラウンド目の文脈の 2 倍 + 余白が窓ちょうどなら続け、窓を 1 超えると新しい会話にする。"""
    _declare(repos, SAVED)
    context = 100000
    fake = Sessions(tmp_path / "st", TWO_FIXES, contexts=(context, context), window=2 * context + _margin() + extra)
    _run(capsys, monkeypatch, fake, wt)
    flag, _ = _session_of(_fixer_argvs(fake)[1])
    assert flag == ("--resume" if resumed else "--session-id")


def test_issue_4013_a_larger_growth_decides_the_next_round(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#4013 の受け入れ条件 2: 続けた 2 ラウンド目の増分が 1 ラウンド目より大きいと、3 ラウンド目はその増分で判断する。"""
    _declare(repos, SAVED)
    # 1 ラウンド目 100000（増分 100000）、2 ラウンド目 300000（増分 200000）。300000 + 200000 + 余白は窓を超える
    window = 300000 + 100000 + _margin()
    fake = Sessions(tmp_path / "st", THREE_FIXES, contexts=(100000, 300000, 300000), window=window)
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "4")
    assert [_session_of(a)[0] for a in _fixer_argvs(fake)] == ["--session-id", "--resume", "--session-id"]
    assert _fixer_notes(got)[-1] == f"指摘 1 件を扱った（新しい会話: {resume_decide.LARGE}）"


def test_issue_4013_an_automatic_summary_stops_continuing(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#4013 の受け入れ条件 2: 1 ラウンド目の出力の途中に自動の要約の行があると、2・3 ラウンド目とも新しい会話にする。"""
    _declare(repos, SAVED)
    script = {"codex": [["一つ目"], ["二つ目"], ["三つ目"], []], "kiro": [[], [], [], []]}
    fake = Sessions(tmp_path / "st", script)
    fake.compact = {0}
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "4")
    assert [_session_of(a)[0] for a in _fixer_argvs(fake)] == ["--session-id", "--session-id", "--session-id"]
    assert _fixer_notes(got)[1:] == [
        f"指摘 1 件を扱った（新しい会話: {resume_decide.COMPACTED}）",
        f"指摘 1 件を扱った（新しい会話: {resume_decide.LARGE}）",
    ]


def test_issue_4013_a_fixer_that_did_not_finish_is_not_continued(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#4013 の受け入れ条件 2: 結果を書いても DONE で終わらなかった修正担当の会話は続けない。"""

    class TimedOutOnce(Sessions):
        def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
            first = self._fixer_runs == 0
            status = super()._fix(spec)
            return agents.AgentStatus(spec.agent, agents.AgentState.TIMEOUT, None, "時間切れ", ()) if first else status

    _declare(repos, SAVED)
    fake = TimedOutOnce(tmp_path / "st", TWO_FIXES)
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert [_session_of(a)[0] for a in _fixer_argvs(fake)] == ["--session-id", "--session-id"]
    assert _fixer_notes(got)[1] == f"指摘 1 件を扱った（新しい会話: {resume_decide.FAILED}）"


def test_issue_4013_a_missing_conversation_is_relaunched_once_as_a_new_one(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#4013 の受け入れ条件 3: 続けた会話が見つからなければ、同じラウンドで `round<n>-fix-new` の新しい会話で 1 度だけ起動し直し、
    修正を失わない。"""
    _declare(repos, SAVED)
    fake = Sessions(tmp_path / "st", TWO_FIXES)
    fake.missing = {1}
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    fixers = [s for s in fake.launches if s.agent == "claude"]
    assert [s.paths.out.name for s in fixers] == ["round1-fix.out", "round2-fix.out", f"round2{fixer.FRESH_SUFFIX}.out"]
    assert [_session_of(list(s.argv))[0] for s in fixers] == ["--session-id", "--resume", "--session-id"]
    assert len({_session_of(list(s.argv))[1] for s in fixers}) == 2
    assert _fixer_notes(got)[1] == f"指摘 1 件を扱った（新しい会話: {fixer.MISSING}）"
    assert [f["outcome"] for f in _state(fake.state_dir)["findings"]] == [fin.FIXED, fin.FIXED]


@pytest.mark.parametrize("failures", [{"plain_fail": {1}}, {"missing": {1, 2}}])
def test_issue_4013_other_failures_are_not_relaunched(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, failures: dict[str, set[int]]
) -> None:
    """#4013 の受け入れ条件 3: 文言の無い失敗と、起動し直した起動の失敗は、起動し直さない（ラウンドは errored）。"""
    _declare(repos, SAVED)
    fake = Sessions(tmp_path / "st", TWO_FIXES)
    for name, runs in failures.items():
        setattr(fake, name, runs)
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["status"] == "stopped"
    assert len(_fixer_argvs(fake)) == (2 if "plain_fail" in failures else 3)
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.ERROR


def test_issue_4013_without_saved_sessions_a_missing_mark_is_not_relaunched(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#4013 の受け入れ条件 7: 会話を保存しない設定では、会話が見つからない文言を出しても起動し直さない。"""
    _declare(repos, {"version": 1, "focus": [], "fixer_sessions": False})
    fake = Sessions(tmp_path / "st", TWO_FIXES)
    fake.missing = {0}
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert len(_fixer_argvs(fake)) == 1
    assert _fixer_notes(got) == [f"結果無し（failed）（新しい会話: {fixer.UNSAVED}）"]


def test_issue_4013_review_settings_default_to_not_saving() -> None:
    assert settings.ReviewSettings().fixer_sessions is False


def test_issue_4013_saved_sessions_are_read_from_a_plain_file_or_a_link_outside_the_worktree(
    repos: Repos, wt: Path, tmp_path: Path
) -> None:
    """#4013: メインディレクトリの通常のファイルと、worktree の外を指すリンクの先の真は受ける。"""
    _declare(repos, SAVED)
    assert settings.read_settings(repos.main, wt).fixer_sessions is True
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(SAVED), encoding="utf-8")
    (repos.main / ".ndf" / "review.json").unlink()
    (repos.main / ".ndf" / "review.json").symlink_to(outside)
    assert settings.read_settings(repos.main, wt).fixer_sessions is True


@pytest.mark.parametrize("link", ["dir", "file"])
def test_issue_4013_saved_sessions_inside_the_reviewed_worktree_stop_before_any_launch(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, link: str
) -> None:
    """#4013: メインディレクトリの `.ndf` か `review.json` が worktree の中を指すリンクで、その先が真なら invalid で止め、
    修正担当を起動しない。偽なら今のとおり読む。"""
    (wt / ".ndf").mkdir(exist_ok=True)
    (wt / ".ndf" / "review.json").write_text(json.dumps(SAVED), encoding="utf-8")
    if link == "dir":
        (repos.main / ".ndf").symlink_to(wt / ".ndf")
    else:
        (repos.main / ".ndf").mkdir()
        (repos.main / ".ndf" / "review.json").symlink_to(wt / ".ndf" / "review.json")
    with pytest.raises(criteria_decide.ReviewDeclInvalid):
        settings.read_settings(repos.main, wt)
    fake = Sessions(tmp_path / "st", TWO_FIXES)
    monkeypatch.setattr(cli, "LAUNCH", fake)
    argv = ["run", "--pr", str(PR), "--state-dir", str(fake.state_dir), "--base", "origin/main", "--worktree", str(wt)]
    code, got = run_cli(capsys, *argv, "--branch", BRANCH, "--reviewer", "codex")
    assert got["cause"]["kind"] == "invalid" and "claude" not in fake.calls
    (wt / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["重点"]}), encoding="utf-8")
    got = settings.read_settings(repos.main, wt)
    assert (got.focus, got.raw, got.fixer_sessions) == (("重点",), (wt / ".ndf" / "review.json").read_bytes(), False)


def test_issue_4013_saved_sessions_in_the_reviewed_worktree_itself_stop(repos: Repos) -> None:
    """#4013: レビューする worktree がメインディレクトリそのものなら、真の設定は止める。"""
    _declare(repos, SAVED)
    with pytest.raises(criteria_decide.ReviewDeclInvalid):
        settings.read_settings(repos.main, repos.main)


def test_issue_4013_fixer_launch_carries_the_session_and_the_output_name(repos: Repos, wt: Path, tmp_path: Path) -> None:
    """#4013: `_fixer_launch` は渡した会話の指定を引数に載せ、出力の名前を `round<n><suffix>` にする。"""
    cfg = settings.RunConfig(wt, BRANCH, "origin/main", PR, tmp_path / "st", False, ("codex",))
    run = drive._Run(cfg, git.Git(wt), store.state_pr(cfg.state_dir, PR), {}, None, [], [], [])  # type: ignore[arg-type]
    plain = fixer._fixer_launch(run, 2, "質問", None)
    assert plain.paths.out.name == "round2-fix.out" and "--no-session-persistence" in plain.argv
    assert _session_of(list(plain.argv)) == (None, None)
    fresh = fixer._fixer_launch(run, 2, "質問", claude.Session("abc", False), fixer.FRESH_SUFFIX)
    assert fresh.paths.out.name == f"round2{fixer.FRESH_SUFFIX}.out" and _session_of(list(fresh.argv)) == ("--session-id", "abc")


# --- _launch_fixer が増分の起点（引き継いだ文脈）に使う前回記録（リファクタリングの前の振る舞いの固定） ---------------


def _unit_run(tmp_path: Path, wt: Path, last: Optional[resume_decide.Previous], growth: int = 0) -> drive._Run:
    """`fixer_sessions` が真の `_Run`（git は使わない）。前回記録 `last` と増分の最大 `growth` を置く。"""
    state_dir = tmp_path / "st"
    state_dir.mkdir(exist_ok=True)
    cfg = settings.RunConfig(
        wt, BRANCH, "origin/main", PR, state_dir, False, ("codex",), settings=settings.ReviewSettings(fixer_sessions=True)
    )
    run = drive._Run(cfg, None, store.state_pr(state_dir, PR), {}, None, [], [], [])  # type: ignore[arg-type]
    run.fixer_last, run.fixer_growth = last, growth
    return run


def _write_result_line(out: Path, context: int, window: int = 1000000) -> None:
    """修正担当の標準出力へ、文脈のトークン数 `context` の結果の行を書く（`Sessions._fix` と同じ形）。"""
    iteration = {"input_tokens": 2, "cache_read_input_tokens": context - 2, "cache_creation_input_tokens": 0}
    models = {"claude-opus-5-5": {"inputTokens": context, "outputTokens": 1, "contextWindow": window}}
    steps = [{"type": "system", "subtype": "init"}, {"type": "result", "usage": {"iterations": [iteration]}, "modelUsage": models}]
    out.write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8")


class _ScriptedLaunch:
    """起動ごとに文脈のトークン数を書く偽の起動。`missing` の回（0 から）は会話が見つからない文言で落ちる。"""

    def __init__(self, contexts: tuple[int, ...], missing: frozenset[int] = frozenset()):
        self.contexts, self.missing, self.specs = contexts, missing, []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        n = len(self.specs)
        self.specs.append(spec)
        if n in self.missing:
            spec.paths.err.write_text(f"Error: {claude.NO_CONVERSATION}: x\n", encoding="utf-8")
            return agents.AgentStatus(spec.agent, agents.AgentState.FAILED, 1, "落ちた", ())
        _write_result_line(spec.paths.out, self.contexts[n])
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())


def _taken(context: int, window: int = 1000000) -> resume_decide.Previous:
    return resume_decide.Previous("prev-id", True, True, False, context, window)


def test_launch_fixer_resumed_conversation_measures_growth_from_the_previous_context(wt: Path, tmp_path: Path) -> None:
    """続けた会話の増分は、前回記録の文脈からの差。前回記録は今回の会話の ID と文脈で置き換わる。"""
    run = _unit_run(tmp_path, wt, _taken(100000))
    launch = _ScriptedLaunch((130000,))
    status, how = fixer._launch_fixer(run, 2, "質問", tmp_path / "st" / "none.json", launch)
    assert status.state == agents.AgentState.DONE and how == "（会話を続けた）"
    assert _session_of(list(launch.specs[0].argv)) == ("--resume", "prev-id")
    assert run.fixer_growth == 30000
    assert run.fixer_last == resume_decide.Previous("prev-id", True, False, False, 130000, 1000000)


def test_launch_fixer_resumed_growth_keeps_the_larger_earlier_maximum(wt: Path, tmp_path: Path) -> None:
    """続けた会話の増分が前の最大より小さければ、前の最大を残す。"""
    run = _unit_run(tmp_path, wt, _taken(100000), growth=50000)
    fixer._launch_fixer(run, 2, "質問", tmp_path / "st" / "none.json", _ScriptedLaunch((130000,)))
    assert run.fixer_growth == 50000


def test_launch_fixer_resumed_growth_does_not_go_negative(wt: Path, tmp_path: Path) -> None:
    """続けた会話の文脈が前回記録より小さければ、増分は 0。"""
    run = _unit_run(tmp_path, wt, _taken(100000))
    fixer._launch_fixer(run, 2, "質問", tmp_path / "st" / "none.json", _ScriptedLaunch((90000,)))
    assert run.fixer_growth == 0


def test_launch_fixer_new_conversation_ignores_the_previous_context(wt: Path, tmp_path: Path) -> None:
    """続けないと決めた（前回の結果をマージできなかった）ときは、前回記録の文脈を起点にせず今回の文脈を丸ごと増分にする。"""
    previous = resume_decide.Previous("prev-id", True, False, False, 100000, 1000000)
    run = _unit_run(tmp_path, wt, previous)
    launch = _ScriptedLaunch((130000,))
    _, how = fixer._launch_fixer(run, 2, "質問", tmp_path / "st" / "none.json", launch)
    assert how == f"（新しい会話: {resume_decide.NOT_TAKEN}）"
    flag, sid = _session_of(list(launch.specs[0].argv))
    assert flag == "--session-id" and sid != "prev-id"
    assert run.fixer_growth == 130000
    assert run.fixer_last is not None and run.fixer_last.session_id == sid and run.fixer_last.context == 130000


def test_launch_fixer_first_launch_counts_the_whole_context(wt: Path, tmp_path: Path) -> None:
    """前回記録が無い最初の起動は、今回の文脈を丸ごと増分にする。"""
    run = _unit_run(tmp_path, wt, None)
    _, how = fixer._launch_fixer(run, 1, "質問", tmp_path / "st" / "none.json", _ScriptedLaunch((70000,)))
    assert how == f"（新しい会話: {resume_decide.FIRST}）"
    assert run.fixer_growth == 70000


def test_launch_fixer_relaunch_after_missing_conversation_does_not_use_the_previous_context(wt: Path, tmp_path: Path) -> None:
    """続けた会話が見つからず新しい会話で起動し直したときは、前回記録の文脈を起点にせず、起動し直した方の文脈を丸ごと
    増分にし、前回記録も起動し直した方にする。"""
    run = _unit_run(tmp_path, wt, _taken(100000))
    launch = _ScriptedLaunch((0, 130000), missing=frozenset({0}))
    status, how = fixer._launch_fixer(run, 2, "質問", tmp_path / "st" / "none.json", launch)
    assert status.state == agents.AgentState.DONE and how == f"（新しい会話: {fixer.MISSING}）"
    assert [_session_of(list(s.argv))[0] for s in launch.specs] == ["--resume", "--session-id"]
    assert launch.specs[1].paths.out.name == f"round2{fixer.FRESH_SUFFIX}.out"
    assert run.fixer_growth == 130000
    fresh_id = _session_of(list(launch.specs[1].argv))[1]
    assert run.fixer_last == resume_decide.Previous(fresh_id, True, False, False, 130000, 1000000)
