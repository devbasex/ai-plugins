"""収束ループの駆動 `ndf cross-review run`。相手役と修正担当は偽物に差し替え、git は一時の origin で本物を打つ。

偽の相手役はラウンドごとの台本の指摘を最後の 1 行の JSON で出し、偽の修正担当は問いの id を 1 件ずつ直して
（ファイルを書いてコミットし）、修正の結果ファイルを書く。
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import agents, git
from ndf.contracts import result
from ndf.domains.crossreview import cli, drive, store
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, run_cli, sh

PR = 41
FAIL = "fail"
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
        body = {"findings": [{"text": t, "section": ""} for t in said]}
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


@pytest.fixture
def wt(repos: Repos, tmp_path: Path) -> Path:
    commit(repos.other, "doc.md", "# 設計\n\n## 節 A\n\n本文\n\n## 節 B\n\n足した\n", "節 B")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    return repos.add_worktree(tmp_path / "wt")


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
    fake = Fake(tmp_path / "st", {"codex": [["節 B の根拠が無い"], []], "kiro": [["節 B の根拠が無い", "語の定義"], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], got["metrics"]) == (0, "ok", {"rounds": 2, "findings": 2})
    assert got["outputs"] == {"gaps": []}
    state = _state(fake.state_dir)
    assert [r["verdict"] for r in state["rounds"]] == [drive.CHANGES_REQUIRED, fin.CONVERGED]
    assert [r["scope"] for r in state["rounds"]] == ["full", "since"]
    assert "doc.md#節 B" in state["reviewed_sections"]
    assert sh(repos.origin, "rev-parse", BRANCH).strip() == sh(wt, "rev-parse", "HEAD").strip()
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["final"] == fin.CONVERGED
    assert sorted(fake.calls) == ["claude", "codex", "codex", "kiro", "kiro"]


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


def test_one_reviewer_failing_is_a_warning(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [FAIL], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert any("codex" in w for w in got["warnings"])


def test_every_reviewer_failing_in_round_one_is_work_stopped(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [FAIL], "kiro": [FAIL]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "work_stopped"


def test_oscillation_stops_and_reopens_the_returned_finding(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """前々ラウンドの指摘が前ラウンドで消えて戻ったら、上限を待たずに振動で止め、戻った指摘を欠けに残す。"""
    fake = Fake(tmp_path / "st", {"codex": [["A を直す"], ["B を直す"], ["A を直す"]], "kiro": [[], [], []]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--max-rounds", "5")
    assert got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["r1-1"]
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.OSCILLATING


def test_issue_2440_push_rejected_by_another_push_is_taken_in(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2440: 修正中に PR のブランチへ別の push が入っても、取り込んで送り直し、止まらない。"""
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
    """#2440: 取り込みで衝突したら取り消して止め、届かなかった修正の指摘を未解決（欠け）へ戻す。強制の push はしない。"""
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
    """#2770: 前の起動が修正必須のラウンドの後に修正の結果ファイルだけを残していれば、始めに取り込んで次のラウンドへ進む。"""
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


def test_run_config_resolves_relative_paths_from_the_caller(tmp_path: Path, monkeypatch) -> None:
    """PR #3466 の指摘: 相対の --state-dir と --worktree は打った場所の cwd を基準に絶対パスへそろえて子へ渡す。"""
    monkeypatch.chdir(tmp_path)
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


# --- 現状固定テスト: 1 回の起動に重ねる値（drive._Run の台帳・過程・注意・状態ファイル・要約。I-014） ---


class Recording(Fake):
    """起動ごとに（相手役、出力の名前、問い）を残す偽物。"""

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
    """ラウンドの記録から、コミットの時刻で変わる SHA（head・commit・pushed）を外す。"""
    out = []
    for r in state["rounds"]:
        row = {k: v for k, v in r.items() if k != "head"}
        if "fix" in row:
            row["fix"] = {k: v for k, v in row["fix"].items() if k not in ("commit", "pushed")}
        out.append(row)
    return out


def test_run_records_the_ledger_process_and_summary(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Recording(tmp_path / "st", {"codex": [["節 B の根拠が無い"], []], "kiro": [["節 B の根拠が無い", "語の定義"], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["summary"] == "2 ラウンドで converged。指摘 2 件（直した 2・見送った 0・未解決 0）"
    assert got["items"] == [
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
        _entry("r1-1", "codex", "節 B の根拠が無い", fin.FIXED, 1),
        _entry("r1-2", "kiro", "語の定義", fin.FIXED, 1),
    ]
    assert state["pr"] == PR
    assert state["reviewed_sections"] == ["doc.md#節 A", "doc.md#節 B", "doc.md#設計"]
    assert _rounds_without_heads(state) == [
        {"fix": {"fixed": ["r1-1", "r1-2"], "reported": 2}, "round": 1, "scope": "full", "verdict": drive.CHANGES_REQUIRED},
        {"round": 2, "scope": "since", "verdict": fin.CONVERGED},
    ]
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME) == {
        "final": fin.CONVERGED,
        "findings": [
            {"id": "r1-1", "outcome": fin.FIXED, "reason": None, "text": "節 B の根拠が無い"},
            {"id": "r1-2", "outcome": fin.FIXED, "reason": None, "text": "語の定義"},
        ],
        "pr": PR,
        "rounds": 2,
    }
    # 修正の結果ファイルはラウンドの名前へ移り、残りとして読まれない
    assert (fake.state_dir / "fix-pr41-result-round1.json").is_file()
    assert store.pending_fix_result(fake.state_dir, store.state_pr(fake.state_dir, PR)) is None
    fix_prompt = next(p for a, _, p in fake.prompts if a == "claude")
    assert "- r1-1: 節 B の根拠が無い\n- r1-2: 語の定義\n" in fix_prompt
    assert str(store.fix_result_path(fake.state_dir, store.state_pr(fake.state_dir, PR))) in fix_prompt


def test_run_lists_deferred_findings_in_the_next_review_prompt_and_does_not_reenter_them(
    wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """見送った指摘は次のラウンドの問いに「見送り済み」として載り、出し直されても台帳へ入らない。"""
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
    """修正担当が結果を書かなければ、注意と過程に載せて errored で止め、指摘は未解決のまま状態ファイルに残す。"""

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
    assert _rounds_without_heads(state) == [{"round": 1, "scope": "full", "verdict": drive.CHANGES_REQUIRED}]
    assert store.read_object(fake.state_dir / drive.SUMMARY_NAME)["final"] == fin.ERROR
