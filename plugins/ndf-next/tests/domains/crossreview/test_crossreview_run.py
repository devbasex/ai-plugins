"""収束ループの駆動 `ndf cross-review run`。相手役と修正担当は偽物に差し替え、git は一時の origin で本物を打つ。

偽の相手役はラウンドごとの台本の指摘を最後の 1 行の JSON で出し、偽の修正担当は問いの id を 1 件ずつ直して
（ファイルを書いてコミットし）、修正の結果ファイルを書く。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import agents
from ndf.contracts import result
from ndf.domains.crossreview import cli, drive, store
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, last_result, sh

PR = 41
FAIL = "fail"
_ROUND = re.compile(r"round(\d+)-")
_ID = re.compile(r"^- (\S+): ", re.MULTILINE)


class Fake:
    """偽の相手役と修正担当。`script[相手役][ラウンド - 1]` は指摘の本文の並びか `FAIL`。"""

    def __init__(self, state_dir: Path, script: dict[str, list[Any]], decide: Optional[Callable[[str], Optional[dict]]] = None):
        self.state_dir = state_dir
        self.script = script
        self.decide = decide if decide is not None else (lambda fid: {"outcome": fin.FIXED})
        self.after_commit: Optional[Callable[[], None]] = None
        self.calls: list[str] = []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        self.calls.append(spec.agent)
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        if spec.agent == "claude":
            return self._fix(spec)
        round_no = int(_ROUND.search(spec.paths.out.name).group(1))
        lines = self.script[spec.agent]
        said = lines[round_no - 1] if round_no <= len(lines) else []
        if said == FAIL:
            return agents.AgentStatus(spec.agent, agents.AgentState.FAILED, 1, "落ちた", ())
        body = {"findings": [{"text": t, "section": ""} for t in said]}
        spec.paths.out.write_text(f"考えた {{途中}}\n{json.dumps(body, ensure_ascii=False)}\n", encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    def _fix(self, spec: agents.Launch) -> agents.AgentStatus:
        prompt = spec.argv[-1]
        ids = _ID.findall(prompt)
        n = len(list(self.state_dir.glob("round*-fix.out")))
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
    code = cli.main(argv)
    got = last_result(capsys.readouterr().out)
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
    rebuild.write_text(json.dumps(result.result("rebuild", "ok", "s", [{"id": "R1", "text": "出すコミット"}, {"id": "R2", "text": "対応"}])))
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
    assert got["gaps"] == [{"kind": fin.GAP_UNHANDLED, "name": "rebuild-1", "reason": "レビューで直したとも見送ったとも決めていない: 記録の行のキー"}]


def test_issue_2770_leftover_fix_result_is_taken_in_on_restart(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#2770: 前の起動が修正必須のラウンドの後に修正の結果ファイルだけを残していれば、始めに取り込んで次のラウンドへ進む。"""
    state_dir = tmp_path / "st"
    ref = store.state_pr(state_dir, PR)
    finding = {"id": "r1-1", "key": "k", "text": "直す", "section": None, "round": 1, "outcome": fin.UNRESOLVED, "reason": None}
    head = sh(wt, "rev-parse", "HEAD").strip()
    store.write_object(ref.path, {"pr": PR, "rounds": [{"round": 1, "verdict": drive.CHANGES_REQUIRED, "head": head}], "findings": [finding]})
    store.fix_result_path(state_dir, ref).write_text(json.dumps({"pr": PR, "findings": [{"id": "r1-1", "outcome": fin.FIXED}]}))
    fake = Fake(state_dir, {"codex": [[], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    state = _state(state_dir)
    assert state["rounds"][0]["fix"]["reported"] == 1 and state["findings"][0]["outcome"] == fin.FIXED
    assert store.pending_fix_result(state_dir, ref) is None


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
    assert cli.main(argv) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
