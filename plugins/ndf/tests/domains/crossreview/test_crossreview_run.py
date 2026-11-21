"""収束ループの駆動 `ndf cross-review run`。相手役と修正担当は偽物に差し替え、git は一時の origin で本物を打つ。

偽の相手役はラウンドごとの台本の指摘を最後の 1 行の JSON で出し、偽の修正担当は質問の id を 1 件ずつ直して
（ファイルを書いてコミットし）、修正の結果ファイルを書く。
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional


from ndf.adapters import agents, git
from ndf.contracts import result
from ndf.domains.crossreview import cli, drive, store
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, run_cli, sh

PR = 41
FAIL = "fail"
# 相手役のサンドボックスがコマンドを打てず、読めなかったことを指摘として書いて終わる（#3893）
SANDBOX = "sandbox"
SANDBOX_ERR = "bwrap: No permissions to create a new namespace, likely because the kernel does not allow non-privileged user namespaces\n"
_ID = re.compile(r"^- (\S+): ", re.MULTILINE)


class Fake:
    """偽の相手役と修正担当。`script[相手役][n]` は相手役の n 回目（0 から）の起動の指摘の本文の並びか `FAIL`。

    相手役は毎ラウンド 1 回ずつ起動されるので、n はラウンドの番号 - 1 にあたる。台本の位置と修正の回数は偽物が
    数える（相手役は並べて起動されるので、数える値はロックで守る）。
    """

    def __init__(self, state_dir: Path, script: dict[str, list[Any]], decide: Optional[Callable[[str], Optional[dict]]] = None):
        self.state_dir = state_dir
        self.script = script
        self.decide = decide if decide is not None else (lambda fid: {"outcome": fin.FIXED})
        self.after_commit: Optional[Callable[[], None]] = None
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._said: dict[str, int] = {}
        self._fixes = 0

    def _next_lines(self, agent: str) -> Any:
        """相手役の台本の次の 1 回分。台本が尽きたら指摘無し。"""
        with self._lock:
            at = self._said.get(agent, 0)
            self._said[agent] = at + 1
        lines = self.script[agent]
        return lines[at] if at < len(lines) else []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        self.calls.append(spec.agent)
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        if spec.agent == "claude":
            return self._fix(spec)
        said = self._next_lines(spec.agent)
        if said == FAIL:
            return agents.AgentStatus(spec.agent, agents.AgentState.FAILED, 1, "落ちた", ())
        if said == SANDBOX:
            spec.paths.err.write_text(SANDBOX_ERR, encoding="utf-8")
            said = ["round の差分と worktree を読み取れませんでした。レビューは未実施です"]
        # 台本の指摘は本文の文字列か、相手役の出力の形の辞書（重要度・位置つき。#3941）
        body = {"findings": [{"text": t, "section": ""} if isinstance(t, str) else t for t in said]}
        spec.paths.out.write_text(f"考えた {{途中}}\n{json.dumps(body, ensure_ascii=False)}\n", encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
        prompt = spec.argv[-1]
        ids = _ID.findall(prompt)
        n = self._fixes
        self._fixes += 1
        sha = commit(spec.cwd, f"fix{n}.txt", "\n".join(ids), f"直す {n}")
        if self.after_commit is not None:
            self.after_commit()
        decided = [{"id": i, **d} for i in ids if (d := self.decide(i)) is not None]
        ref = store.state_pr(self.state_dir, PR)
        out = {"pr": ref.origin_pr, "commit": sha, "findings": decided}
        store.fix_result_path(self.state_dir, ref).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        spec.paths.out.write_text("{}", encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())


def _run(capsys, monkeypatch, fake: Fake, wt: Path, *extra: str) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "LAUNCH", fake)
    argv = ["run", "--pr", str(PR), "--state-dir", str(fake.state_dir), "--base", "origin/main", "--worktree", str(wt)]
    argv += ["--branch", BRANCH, "--reviewer", "codex", "--reviewer", "kiro", *extra]
    code, got = run_cli(capsys, *argv)
    assert result.errors(got, code) == []
    return code, got


def _state(state_dir: Path) -> dict:
    return store.read_object(store.state_pr(state_dir, PR).path)


def test_loop_fixes_pushes_and_converges(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """指摘を重ねずに 1 回直して送り、次のラウンドで指摘が無ければ収束する。rounds・reviewed_sections・summary.json を残す。"""
    fake = Fake(tmp_path / "st", {"codex": [["セクション B の根拠が無い"], []], "kiro": [["セクション B の根拠が無い", "語の定義"], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], got["metrics"]) == (0, "ok", {"rounds": 2, "findings": 2})
    assert got["outputs"] == {"gaps": []}
    state = _state(fake.state_dir)
    assert [r["verdict"] for r in state["rounds"]] == [drive.CHANGES_REQUIRED, fin.CONVERGED]
    assert [r["scope"] for r in state["rounds"]] == ["full", "since"]
    assert "doc.md#セクション B" in state["reviewed_sections"]
    assert sh(repos.origin, "rev-parse", BRANCH).strip() == sh(wt, "rev-parse", "HEAD").strip()
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["final"] == fin.CONVERGED
    assert sorted(fake.calls) == ["claude", "codex", "codex", "kiro", "kiro"]


def test_reviewed_out_is_the_head_the_last_round_read_not_the_fix(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3888 の指摘: 最後のラウンドで修正のコミットを作って incomplete で終えても、--reviewed-out にはレビュー担当が
    読んだ（修正の前の）HEAD を書く。始めに前の起動の値を消す。"""
    before = sh(wt, "rev-parse", "HEAD").strip()
    out = tmp_path / "reviewed-head"
    out.write_text("0" * 40 + "\n", encoding="utf-8")
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt, "--max-rounds", "1", "--reviewed-out", str(out))
    after = sh(wt, "rev-parse", "HEAD").strip()
    assert after != before
    assert out.read_text().strip() == before == _state(fake.state_dir)["rounds"][-1]["head"]


def test_reviewed_out_is_not_written_when_no_reviewer_answers_this_run(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3888 の r2-1: 前の起動のラウンドが状態ファイルに残っていても、今回の起動で全員が無回答なら --reviewed-out を
    書かない（前の起動の head を復活させない）。"""
    out = tmp_path / "reviewed-head"
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [["一つ目"]], "kiro": [[]]}), wt, "--max-rounds", "1", "--reviewed-out", str(out))
    assert out.read_text().strip() == _state(state_dir)["rounds"][-1]["head"]
    code, got = _run(capsys, monkeypatch, Fake(state_dir, {"codex": [FAIL, FAIL], "kiro": [FAIL, FAIL]}), wt, "--reviewed-out", str(out))
    # 全員が読めなければ precondition で止まる（#3893）
    assert got["cause"]["kind"] == "precondition"
    assert not out.exists()


def test_issue_2370_limit_without_unresolved_moves_on(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2370: 上限で終わっても未解決が 0 件なら ok で次の工程へ進み、理由を decisions に残す。"""
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], ["二つ目"]], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "2")
    assert (code, got["status"]) == (0, "ok")
    assert any(fin.MAX_ROUNDS in d["decision"] for d in got["decisions"])


def test_issue_2370_limit_with_unresolved_is_incomplete_with_gaps_in_outputs(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2370・PR #3462 の保留: 見送りに理由が無ければ incomplete で、gaps を outputs にも載せてプランの結果へ引き継ぐ。"""
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"]], "kiro": [[]]}, decide=lambda fid: {"outcome": fin.SKIPPED})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "1")
    assert got["cause"]["kind"] == "incomplete"
    assert got["outputs"]["gaps"] == got["gaps"] and got["gaps"][0]["kind"] == fin.GAP_NO_REASON


def test_one_reviewer_failing_once_is_relaunched_in_the_same_round(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3941: 読めなかった相手役は同じラウンドで 1 度だけ起動し直し、読めればそのラウンドの結果に数える。"""
    fake = Fake(tmp_path / "st", {"codex": [FAIL, []], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert sorted(fake.calls) == ["codex", "codex", "kiro"]
    assert any("codex" in w for w in got["warnings"])
    assert {"run": "round 1 codex", "result": "指摘 0 件（起動し直した）"} in got["process"]


def test_issue_3941_reviewer_failing_twice_does_not_converge(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3941: 起動し直しても読めない相手役がいれば、残りの相手役が 0 件でも収束にせず incomplete_reviewers・work_stopped で止める。"""
    fake = Fake(tmp_path / "st", {"codex": [FAIL, FAIL], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "work_stopped")
    assert fin.INCOMPLETE_REVIEWERS in got["summary"]
    assert any("起動し直しても差分を読めなかった" in w and "codex login status" in w for w in got["warnings"])
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.INCOMPLETE_REVIEWERS
    assert "claude" not in fake.calls


def test_every_reviewer_failing_in_round_one_is_precondition(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3893: 相手役が誰も差分を読めなければ、収束とも誤りとも判定せず precondition で止め、相手役ごとの理由と確かめ方を返す。"""
    fake = Fake(tmp_path / "st", {"codex": [FAIL, FAIL], "kiro": [FAIL, FAIL]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert "codex login status" in got["cause"]["detail"] and "kiro-cli whoami" in got["cause"]["detail"]


def test_issue_3893_sandbox_failure_with_another_reviewer_failing_is_precondition(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3893: codex のサンドボックスが名前空間を作れず（stderr の `bwrap:`）、kiro も回答しなければ、ok で収束せず precondition で止める。
    読めなかったことを書いた回答は指摘として扱わない。"""
    fake = Fake(tmp_path / "st", {"codex": [SANDBOX, SANDBOX], "kiro": [FAIL, FAIL]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert "bwrap:" in got["cause"]["detail"] and "名前空間" in got["cause"]["detail"]
    assert "claude" not in fake.calls
    # 読めなかったラウンドは状態に残さない（指摘も入らない）ので、直して打ち直せば round 1 から続く
    assert not store.state_pr(fake.state_dir, PR).path.is_file()


def test_issue_3893_sandbox_failure_of_one_reviewer_is_a_warning_and_not_a_finding(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3893: サンドボックスで読めなかった相手役の回答は指摘に入れず、注意に載せる。ほかの相手役が読めていれば進む。"""
    fake = Fake(tmp_path / "st", {"codex": [SANDBOX, []], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], got["metrics"]["findings"]) == (0, "ok", 0)
    assert any("codex" in w and "bwrap:" in w for w in got["warnings"])


def test_oscillation_stops_and_reopens_the_returned_finding(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """前々ラウンドの指摘が前ラウンドで消えて戻ったら、上限を待たずに振動で止め、戻った指摘を欠けに残す。"""
    fake = Fake(tmp_path / "st", {"codex": [["A を直す"], ["B を直す"], ["A を直す"]], "kiro": [[], [], []]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "5")
    assert got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["r1-1"]
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.OSCILLATING


def test_issue_2440_push_rejected_by_another_push_is_taken_in(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2440: 修正中に PR のブランチへ別の push が入っても、マージして送り直し、止まらない。"""
    fake = Fake(tmp_path / "st", {"codex": [["直す"], []], "kiro": [[], []]})

    def other_push() -> None:
        sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
        commit(repos.other, "other.txt", "別の書き手", "別の push")
        sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")

    fake.after_commit = other_push
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    tree = sh(repos.origin, "ls-tree", "--name-only", BRANCH)
    assert "other.txt" in tree and "fix0.txt" in tree


def test_issue_2440_conflicting_push_reopens_the_fixed_findings(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2440: マージで衝突したら取り消して止め、届かなかった修正の指摘を未解決（欠け）へ戻す。強制の push はしない。"""
    fake = Fake(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})

    def conflicting() -> None:
        sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
        commit(repos.other, "fix0.txt", "衝突する中身", "衝突")
        sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")

    fake.after_commit = conflicting
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "incomplete" and got["gaps"][0]["name"] == "r1-1"
    assert any("diverged" in w for w in got["warnings"])
    assert sh(repos.origin, "rev-parse", BRANCH).strip() == sh(repos.other, "rev-parse", "HEAD").strip()


def test_issue_2444_rebuild_items_are_each_fixed_or_deferred(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2444 の残り: 組み直しの「足すべき内容」は、相手役の指摘が無くても修正担当へ渡り、1 件ずつ「直した」
    「見送った（理由）」として結果の decisions に載る。"""
    rebuild = tmp_path / "rebuild.json"
    rebuild.write_text(
        json.dumps(result.result("rebuild", "ok", "s", [{"id": "R1", "text": "出すコミット"}, {"id": "R2", "text": "対応"}]))
    )
    decide = {"R1": {"outcome": fin.FIXED}, "R2": {"outcome": fin.SKIPPED, "reason": "別の設計で決める"}}
    fake = Fake(tmp_path / "st", {"codex": [[], []], "kiro": [[], []]}, decide=decide.get)
    code, got = _run(capsys, monkeypatch, fake, wt, "--findings", str(rebuild))
    assert (code, got["status"]) == (0, "ok")
    assert [d["decision"] for d in got["decisions"]] == ["足すべき内容 R1 を直した", "足すべき内容 R2 を見送った（理由: 別の設計で決める）"]


def test_issue_2444_rebuild_item_never_decided_is_a_gap(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    rebuild = tmp_path / "rebuild.json"
    rebuild.write_text(json.dumps(result.result("rebuild", "ok", "s", ["記録の行のキー"])))
    fake = Fake(tmp_path / "st", {"codex": [[], []], "kiro": [[], []]}, decide=lambda fid: None)
    code, got = _run(capsys, monkeypatch, fake, wt, "--findings", str(rebuild), "--max-rounds", "2")
    assert got["cause"]["kind"] == "incomplete"
    assert got["gaps"] == [
        {"kind": fin.GAP_UNHANDLED, "name": "rebuild-1", "reason": "レビューで直したとも見送ったとも決めていない: 記録の行のキー"}
    ]


def test_issue_2770_leftover_fix_result_is_taken_in_on_restart(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2770: 前の起動が修正必須のラウンドの後に修正の結果ファイルだけを残していれば、始めにマージして次のラウンドへ進む。"""
    state_dir = tmp_path / "st"
    ref = store.state_pr(state_dir, PR)
    finding = {"id": "r1-1", "key": "k", "text": "直す", "section": None, "round": 1, "outcome": fin.UNRESOLVED, "reason": None}
    head = sh(wt, "rev-parse", "HEAD").strip()
    store.write_object(
        ref.path, {"pr": PR, "rounds": [{"round": 1, "verdict": drive.CHANGES_REQUIRED, "head": head}], "findings": [finding]}
    )
    store.fix_result_path(state_dir, ref).write_text(json.dumps({"pr": PR, "findings": [{"id": "r1-1", "outcome": fin.FIXED}]}))
    fake = Fake(state_dir, {"codex": [[], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    state = _state(state_dir)
    assert state["rounds"][0]["fix"]["reported"] == 1 and state["findings"][0]["outcome"] == fin.FIXED
    assert store.pending_fix_result(state_dir, ref) is None


def test_push_error_keeps_the_fix_record_and_the_restart_sends_it(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: push が例外（hook・通信）で止まっても修正の記録は状態ファイルに残り、次の起動は未送信の
    修正を送ってから次のラウンドへ進む。"""
    sent = drive.push.push_fix

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise git.GitError("pre-push の hook が拒んだ")

    monkeypatch.setattr(drive.push, "push_fix", broken)
    first = Fake(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, first, wt)
    assert got["cause"]["kind"] == "precondition"
    ref = store.state_pr(first.state_dir, PR)
    assert _state(first.state_dir)["rounds"][0]["fix"]["fixed"] == ["r1-1"]
    assert store.pending_fix_result(first.state_dir, ref) is None
    monkeypatch.setattr(drive.push, "push_fix", sent)
    again = Fake(first.state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "pushed" in _state(first.state_dir)["rounds"][0]["fix"]
    assert sh(repos.origin, "rev-parse", BRANCH).strip() == sh(wt, "rev-parse", "HEAD").strip()
    assert "fix0.txt" in sh(repos.origin, "ls-tree", "--name-only", BRANCH)


def test_resend_after_a_refused_push_restores_the_fixed_findings(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: 送れずに未解決へ戻した指摘は、次の起動の送り直しが届いたら直したへ戻し、修正担当へ渡し直さない。"""
    sent = drive.push.push_fix

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        return drive.push.PushResult(False, True, None, None, drive.push.REJECTED_TWICE, ())

    monkeypatch.setattr(drive.push, "push_fix", refused)
    first = Fake(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})
    _run(capsys, monkeypatch, first, wt)
    assert _state(first.state_dir)["findings"][0]["outcome"] == fin.UNRESOLVED
    monkeypatch.setattr(drive.push, "push_fix", sent)
    again = Fake(first.state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")
    assert _state(first.state_dir)["findings"][0]["outcome"] == fin.FIXED and "claude" not in again.calls
    assert "fix0.txt" in sh(repos.origin, "ls-tree", "--name-only", BRANCH)


def test_run_config_resolves_relative_paths_from_the_caller(tmp_path: Path, monkeypatch) -> None:
    """PR #3466 の指摘: 相対の --state-dir と --worktree は打った場所の cwd を基準に絶対パスへそろえて子へ渡す。"""
    monkeypatch.chdir(tmp_path)
    # 一時の場所は git の作業場所でないので、ランタイムの設定は無い扱いにする
    monkeypatch.setattr(cli, "_policy", lambda wt: cli.Policy(wt, None))
    argv = ["run", "--pr", str(PR), "--state-dir", "st", "--worktree", "wt", "--branch", BRANCH, "--base", "origin/main"]
    cfg = cli._run_config(cli._parser().parse_args([*argv, "--reviewer", "codex", "--reviewer", "kiro"]))
    assert (cfg.worktree, cfg.state_dir) == (tmp_path.resolve() / "wt", tmp_path.resolve() / "st")


def test_issue_2770_changes_required_without_any_fix_stops(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    state_dir = tmp_path / "st"
    ref = store.state_pr(state_dir, PR)
    store.write_object(ref.path, {"pr": PR, "rounds": [{"round": 1, "verdict": drive.CHANGES_REQUIRED, "head": "x"}]})
    fake = Fake(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "work_stopped" and fake.calls == []


def test_issue_3109_unregistered_worktree_stops_with_the_path(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3109: 管理情報の消えた worktree では「リポジトリを決められない」と書かず、worktree と .git が指す先で止まる。"""
    gitdir = Path((wt / ".git").read_text().split(":", 1)[1].strip())
    (gitdir / "HEAD").unlink()
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "precondition" and str(wt) in got["cause"]["detail"]
    assert "リポジトリを決められない" not in got["cause"]["detail"]


def test_unknown_reviewer_is_invalid(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "LAUNCH", Fake(tmp_path / "st", {}))
    argv = ["run", "--pr", "1", "--state-dir", str(tmp_path / "st"), "--base", "origin/main", "--worktree", str(wt), "--reviewer", "nobody"]
    code, got = run_cli(capsys, *argv)
    assert code != 0
    assert got["cause"]["kind"] == "invalid"


# --- 現状固定テスト: 1 回の起動に重ねる値（drive._Run のジャーナル・過程・注意・状態ファイル・要約。I-014） ---


class Recording(Fake):
    """起動ごとに（相手役、出力の名前、質問）を残す偽物。"""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.prompts: list[tuple[str, str, str]] = []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        self.prompts.append((spec.agent, spec.paths.out.name, spec.argv[-1]))
        return super().__call__(spec)


def _entry(fid: str, by: str, text: str, outcome: str, round_no: int, reason: Optional[str] = None) -> dict:
    return {
        "by": by,
        "id": fid,
        "key": text.lower(),
        "outcome": outcome,
        "reason": reason,
        "round": round_no,
        "section": None,
        "text": text,
    }


def _rounds_without_heads(state: dict) -> list[dict]:
    """ラウンドの記録から、コミットの時刻で変わる SHA（head・commit・pushed）と、どのテストも同じ比較基点（base。merge-base の
    SHA）と相手役の並び（reviewers）と重点（focus）を外す。"""
    out = []
    for r in state["rounds"]:
        assert re.fullmatch(r"[0-9a-f]{40}", r["base"]) and r["reviewers"] == ["codex", "kiro"] and r["focus"] == []
        row = {k: v for k, v in r.items() if k not in ("head", "base", "reviewers", "focus")}
        if "fix" in row:
            row["fix"] = {k: v for k, v in row["fix"].items() if k not in ("commit", "pushed")}
        out.append(row)
    return out


def test_run_records_the_journal_process_and_summary(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Recording(
        tmp_path / "st", {"codex": [["セクション B の根拠が無い"], []], "kiro": [["セクション B の根拠が無い", "語の定義"], []]}
    )
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["summary"] == "2 ラウンドで converged。指摘 2 件（直した 2・見送った 0・未解決 0）"
    assert [i for i in got["items"] if i["kind"] == "finding"] == [
        {"kind": "finding", "id": "r1-1", "outcome": fin.FIXED},
        {"kind": "finding", "id": "r1-2", "outcome": fin.FIXED},
    ]
    assert got["process"] == [
        {"run": "round 1 codex", "result": "指摘 1 件"},
        {"run": "round 1 kiro", "result": "指摘 2 件"},
        {"run": "round 1 claude", "result": "指摘 2 件を扱った"},
        {"run": "round 2 codex", "result": "指摘 0 件"},
        {"run": "round 2 kiro", "result": "指摘 0 件"},
    ]
    assert "warnings" not in got
    state = _state(fake.state_dir)
    assert state["findings"] == [
        _entry("r1-1", "codex", "セクション B の根拠が無い", fin.FIXED, 1),
        _entry("r1-2", "kiro", "語の定義", fin.FIXED, 1),
    ]
    assert state["pr"] == PR
    assert state["reviewed_sections"] == ["doc.md#セクション A", "doc.md#セクション B", "doc.md#設計"]
    assert _rounds_without_heads(state) == [
        {"fix": {"fixed": ["r1-1", "r1-2"], "reported": 2}, "round": 1, "scope": "full", "verdict": drive.CHANGES_REQUIRED},
        {"round": 2, "scope": "since", "verdict": fin.CONVERGED},
    ]
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME) == {
        "final": fin.CONVERGED,
        "findings": [
            {"id": "r1-1", "outcome": fin.FIXED, "reason": None, "severity": None, "text": "セクション B の根拠が無い"},
            {"id": "r1-2", "outcome": fin.FIXED, "reason": None, "severity": None, "text": "語の定義"},
        ],
        "pr": PR,
        "rounds": 2,
    }
    # 修正の結果ファイルはラウンドの名前へ移り、残りとして読まれない
    assert (fake.state_dir / "fix-pr41-result-round1.json").is_file()
    assert store.pending_fix_result(fake.state_dir, store.state_pr(fake.state_dir, PR)) is None
    fix_prompt = next(p for a, _, p in fake.prompts if a == "claude")
    assert "- r1-1: セクション B の根拠が無い\n- r1-2: 語の定義\n" in fix_prompt
    assert str(store.fix_result_path(fake.state_dir, store.state_pr(fake.state_dir, PR))) in fix_prompt


def test_run_lists_deferred_findings_in_the_next_review_prompt_and_does_not_reenter_them(
    wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """見送った指摘は次のラウンドの質問に「見送り済み」として載り、出し直されてもジャーナルへ入らない。"""
    skip = {"outcome": fin.SKIPPED, "reason": "別で直す"}
    fake = Recording(
        tmp_path / "st",
        {"codex": [["一つ目", "二つ目"], ["二つ目"]], "kiro": [[], []]},
        decide=lambda fid: skip if fid == "r1-2" else {"outcome": fin.FIXED},
    )
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["summary"]) == (0, "2 ラウンドで converged。指摘 2 件（直した 1・見送った 1・未解決 0）")
    assert got["process"][3] == {"run": "round 2 codex", "result": "指摘 1 件"}
    assert _state(fake.state_dir)["findings"] == [
        _entry("r1-1", "codex", "一つ目", fin.FIXED, 1),
        _entry("r1-2", "codex", "二つ目", fin.SKIPPED, 1, "別で直す"),
    ]
    prompts = {name: p for _, name, p in fake.prompts}
    assert "見送り済み" not in prompts["round1-codex.out"]
    assert "理由を添えて見送り済みの指摘（出し直さない）:\n- 二つ目\n" in prompts["round2-codex.out"]
    assert "理由を添えて見送り済みの指摘（出し直さない）:\n- 二つ目\n" in prompts["round2-kiro.out"]


def test_run_reopens_a_fixed_finding_that_returns(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """直した指摘が次のラウンドで戻れば、同じ id のまま未解決へ戻して round を書き換え、もう一度修正担当へ渡す。"""
    fake = Recording(tmp_path / "st", {"codex": [["一つ目"], ["一つ目"]], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "2")
    assert (code, got["summary"]) == (0, "2 ラウンドで max_rounds。指摘 1 件（直した 1・見送った 0・未解決 0）")
    assert got["warnings"] == ["収束ループは max_rounds で終わった（欠けは 0 件）"]
    assert [p["run"] for p in got["process"]] == [
        "round 1 codex",
        "round 1 kiro",
        "round 1 claude",
        "round 2 codex",
        "round 2 kiro",
        "round 2 claude",
    ]
    state = _state(fake.state_dir)
    assert state["findings"] == [_entry("r1-1", "codex", "一つ目", fin.FIXED, 2)]
    assert [r["verdict"] for r in state["rounds"]] == [drive.CHANGES_REQUIRED, drive.CHANGES_REQUIRED]
    assert [p for a, name, p in fake.prompts if a == "claude"][1].count("- r1-1: 一つ目") == 1
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["final"] == fin.MAX_ROUNDS


def test_run_fixer_without_a_result_is_a_warning_and_errored(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """修正担当が結果を書かなければ、注意と過程に載せて errored で止め、指摘は未解決のまま状態ファイルに残す。
    ラウンドも errored で残すので、次の起動は修正の記録の欠けで止まらず、修正担当をもう一度起動して進む（PR #3466 の指摘）。"""

    class NoFix(Recording):
        def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
            return agents.AgentStatus(spec.agent, agents.AgentState.FAILED, 1, "落ちた", ())

    fake = NoFix(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert got["summary"] == "1 ラウンドで errored。指摘 1 件（直した 0・見送った 0・未解決 1）・欠け 1 件"
    assert got["warnings"] == ["round 1 の修正担当が結果を書かなかった（failed: 落ちた）"]
    assert got["process"][-1] == {"run": "round 1 claude", "result": "結果無し（failed）"}
    state = _state(fake.state_dir)
    assert state["findings"] == [_entry("r1-1", "codex", "直す", fin.UNRESOLVED, 1)]
    assert _rounds_without_heads(state) == [{"round": 1, "scope": "full", "verdict": fin.ERROR}]
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["final"] == fin.ERROR
    again = Fake(fake.state_dir, {"codex": [["直す"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "claude" in again.calls and _state(fake.state_dir)["findings"][0]["outcome"] == fin.FIXED


def test_run_takes_in_a_result_written_by_a_fixer_that_timed_out(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: 修正担当が DONE で終わらなくても、結果ファイルを書き終えていればマージする。"""

    class TimedOut(Fake):
        def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
            super()._fix(spec)
            return agents.AgentStatus(spec.agent, agents.AgentState.TIMEOUT, None, "時間切れ", ())

    fake = TimedOut(tmp_path / "st", {"codex": [["直す"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert _state(fake.state_dir)["findings"][0]["outcome"] == fin.FIXED


def test_run_sets_aside_a_stale_fix_result_before_the_fixer_starts(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: 起動の前に残っている結果ファイルは退避し、結果を書かない修正担当の結果として読まない。"""

    class Silent(Fake):
        def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
            return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    state_dir = tmp_path / "st"
    ref = store.state_pr(state_dir, PR)
    state_dir.mkdir()
    stale = store.fix_result_path(state_dir, ref)
    stale.write_text(json.dumps({"pr": PR, "findings": [{"id": "r1-1", "outcome": fin.FIXED}]}), encoding="utf-8")
    store.write_object(ref.path, {"pr": PR})
    fake = Silent(state_dir, {"codex": [["直す"]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["status"] == "stopped" and "結果を書かなかった" in got["warnings"][-1]
    assert _state(state_dir)["findings"][0]["outcome"] == fin.UNRESOLVED
    assert (state_dir / f"{stale.stem}-stale-round1.json").is_file() and not stale.is_file()


def test_run_fix_result_for_another_pr_is_set_aside(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: 番号の合わない修正の結果は退避して errored で止め、次の起動は同じファイルで落ち続けない。"""

    class WrongPr(Recording):
        def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
            ref = store.state_pr(self.state_dir, PR)
            store.fix_result_path(self.state_dir, ref).write_text(json.dumps({"pr": 1, "findings": []}), encoding="utf-8")
            return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    fake = WrongPr(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["status"] == "stopped" and "読み込めない" in got["warnings"][-1]
    ref = store.state_pr(fake.state_dir, PR)
    assert store.pending_fix_result(fake.state_dir, ref) is None
    assert (fake.state_dir / f"{store.fix_result_path(fake.state_dir, ref).stem}-mismatch-round1.json").is_file()
    again = Fake(fake.state_dir, {"codex": [["直す"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")


def test_run_leftover_fix_result_for_another_pr_is_invalid(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR #3466 の指摘: 前の起動が残した番号の合わない結果ファイルは、トレースバックでなく invalid の結果で止める。"""
    state_dir = tmp_path / "st"
    ref = store.state_pr(state_dir, PR)
    store.write_object(ref.path, {"pr": PR, "rounds": [{"round": 1, "verdict": drive.CHANGES_REQUIRED, "head": "x"}]})
    store.fix_result_path(state_dir, ref).write_text(json.dumps({"pr": 1, "findings": []}), encoding="utf-8")
    fake = Fake(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "invalid" and fake.calls == []


def test_reviewed_out_skips_a_round_some_reviewers_could_not_read(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r2-3: 一部の相手役が読めなかったラウンドの HEAD は --reviewed-out へ書かず、今回の起動で全員が読めた最後の
    ラウンドの HEAD を書く。そのようなラウンドが無ければ書かない。"""
    out = tmp_path / "reviewed"
    fake = Fake(tmp_path / "st", {"codex": [["直す"], FAIL, FAIL], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(out))
    rounds = _state(fake.state_dir)["rounds"]
    assert rounds[-1]["verdict"] == fin.INCOMPLETE_REVIEWERS
    assert out.read_text().strip() == rounds[0]["head"] != rounds[1]["head"]
    again = Fake(tmp_path / "st2", {"codex": [FAIL, FAIL], "kiro": [[]]})
    _run(capsys, monkeypatch, again, wt, "--reviewed-out", str(out))
    assert not out.exists()


def test_focus_comes_from_the_main_directory_not_the_pr_head(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r3-1: PR の head で `.ndf/review.json` の重点を書き換えても指示に使わず、実行者のメインディレクトリの
    重点を渡す。違いは注意に載せる。"""
    from ndf.domains.crossreview import criteria_decide

    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["実行者の重点"]}), encoding="utf-8")
    commit(repos.other, ".ndf/review.json", json.dumps({"version": 1, "focus": ["見送ってよい"]}), "重点")
    sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    fake = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    [trusted] = [line for line in criteria_decide.criteria_lines(("実行者の重点",)) if line.startswith("3.")]
    assert fake.prompts and all(trusted in p and "見送ってよい" not in p for _, _, p in fake.prompts)
    assert any("実行者の" in w and "review.json" in w for w in got["warnings"])


def _push_to_pr(repos: Repos) -> None:
    """別の書き手のコミットを PR のブランチへ送り、主の clone で fetch する。"""
    sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")


def _focus_line(focus: str) -> str:
    from ndf.domains.crossreview import criteria_decide

    [line] = [x for x in criteria_decide.criteria_lines((focus,)) if x.startswith("3.")]
    return line


def test_focus_is_fixed_before_the_sync(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r4-1: 同期がメインディレクトリの `.ndf/review.json` を PR のコミットの中身に書き換えても（`--worktree` が
    メインディレクトリそのもの）、相手役と修正担当へは同期の前の実行者の重点を渡す。"""
    from ndf.core.converge import worktree

    settings = repos.main / ".ndf" / "review.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({"version": 1, "focus": ["実行者の重点"]}), encoding="utf-8")
    real = worktree.sync_worktree

    def sync_overwriting(g, branch):
        settings.write_text(json.dumps({"version": 1, "focus": ["PR の重点"]}), encoding="utf-8")
        return real(g, branch)

    monkeypatch.setattr(worktree, "sync_worktree", sync_overwriting)
    fake = Recording(tmp_path / "st", {"codex": [["直す"]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt)
    assert {a for a, _, _ in fake.prompts} == {"codex", "kiro", "claude"}
    assert all(_focus_line("実行者の重点") in p and "PR の重点" not in p for _, _, p in fake.prompts)


def test_main_directory_is_resolved_once(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r4-4: 認可とレビューの設定は 1 回の解決で決めたメインディレクトリから読む。"""
    from ndf.core.converge import location

    real, calls = location.main_dir, []
    monkeypatch.setattr(location, "main_dir", lambda g: calls.append(g) or real(g))
    _run(capsys, monkeypatch, Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]}), wt)
    assert len(calls) == 1


def test_pr_settings_are_compared_from_the_tree_without_following_links(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """PR 3944 の r4-2: PR が `.ndf/review.json` を実行者の設定と同じ中身のファイルへのリンクにしても、リンクをたどらずに
    違いとして知らせる。UTF-8 でない中身でも止めず、違いとして知らせる。"""
    trusted = json.dumps({"version": 1, "focus": ["実行者の重点"]})
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(trusted, encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text(trusted, encoding="utf-8")
    (repos.other / ".ndf").mkdir(exist_ok=True)
    (repos.other / ".ndf" / "review.json").symlink_to(outside)
    sh(repos.other, "add", ".ndf/review.json")
    sh(repos.other, "commit", "-q", "-m", "リンク")
    _push_to_pr(repos)
    code, got = _run(capsys, monkeypatch, Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]}), wt)
    assert (code, got["status"]) == (0, "ok") and any("review.json" in w for w in got["warnings"])
    (repos.other / ".ndf" / "review.json").unlink()
    # 差分では Git がバイナリとして扱う中身（比べる側はデコードしない）
    (repos.other / ".ndf" / "review.json").write_bytes(b"\xff\xfe\x00")
    sh(repos.other, "add", ".ndf/review.json")
    sh(repos.other, "commit", "-q", "-m", "UTF-8 でない")
    _push_to_pr(repos)
    code, got = _run(capsys, monkeypatch, Fake(tmp_path / "st2", {"codex": [[]], "kiro": [[]]}), wt)
    assert (code, got["status"]) == (0, "ok") and any("review.json" in w for w in got["warnings"])


def test_same_settings_in_the_pr_are_not_warned(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r4-2: PR の head の設定が実行者の設定と同じバイト列なら注意に載せない。"""
    trusted = json.dumps({"version": 1, "focus": ["実行者の重点"]})
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(trusted, encoding="utf-8")
    commit(repos.other, ".ndf/review.json", trusted, "同じ設定")
    _push_to_pr(repos)
    code, got = _run(capsys, monkeypatch, Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]}), wt)
    assert (code, got["status"]) == (0, "ok") and not any("review.json" in w for w in got.get("warnings", []))


def test_changed_focus_reviews_the_full_diff_again(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r4-3: 収束した PR を、重点を足して同じ head で打ち直すと、前回の収束を返さず全差分を足した重点で読ませる。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt)
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["足した重点"]}), encoding="utf-8")
    fake = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok") and fake.prompts
    assert all(_focus_line("足した重点") in p and "範囲: full" in p for _, _, p in fake.prompts)
    assert _state(state_dir)["rounds"][-1]["focus"] == ["足した重点"]
    again = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, again, wt)
    assert again.prompts == []


def test_unresolved_policy_location_stops_before_any_launch(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r2-7: ランタイムの設定の保存先（メインディレクトリ）を決められなければ、設定が無いときと分けて、どの担当も
    起動する前に precondition で止める。"""
    from ndf.core.converge import location

    def broken(g):
        raise git.GitError("common dir を読めない")

    monkeypatch.setattr(location, "main_dir", broken)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "precondition" and "allowed" in got["cause"]["detail"]
    assert fake.calls == []
