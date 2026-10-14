"""レビュー範囲と範囲の照合（#1702）。

スプリント PR の形の入力（base から 16 ファイルを変えた複数のコミットの末尾に、1 ファイルだけを変える
cross-refactoring のコミットが 2 件付く）を一時の git リポジトリで作り、次を確かめる。

| 受け入れ条件 | 見るもの |
| --- | --- |
| 1・9 | `start-round` が範囲（base・head・16）を記録し、プロンプトに base・head・3 点の diff・16 が載る |
| 2 | round 2 のプロンプトの head は round 2 の head で、ファイル数は数え直した値 |
| 3 | 設計 PR の 2 ラウンド目は前のラウンドの head からの範囲 |
| 4 | `read_files` が席の記録へ残り、プロンプトに書く指示が載る |
| 5・6・7・9 | 読んだ数が足りない・測れない `APPROVE` で収束せず、修正を挟まずに次のラウンドへ進む |
| 8 | base を取れないときは止まらず、範囲の節なしで起動し、照合しなかったことが出る |
| 10 | `REQUEST_CHANGES` の指摘は読んだ数に関わらず取り込まれる |
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess

import pytest
import review_lib.ci
import review_lib.commands.judge
import review_lib.commands.read_result
import review_lib.commands.start_round
import review_lib.github
import review_lib.review_scope
import review_lib.workspace

PR = 4210
REPO = "o/r"
LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "launch-reviewer.sh"


def _git(cwd: pathlib.Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _commit(repo: pathlib.Path, files: dict[str, str], msg: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


class Sprint:
    """スプリント PR の形のリポジトリ。`base` から 4 課題で 16 ファイル、末尾に 1 ファイルの 2 コミット。"""

    def __init__(self, root: pathlib.Path):
        self.origin = root / "origin.git"
        self.wt = root / "wt"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.wt)], check=True)
        _git(self.wt, "remote", "add", "origin", str(self.origin))
        self.base = _commit(self.wt, {"README.md": "base\n"}, "base")
        for issue in range(4):
            _commit(self.wt, {f"src/i{issue}/f{n}.py": f"{issue}-{n}\n" for n in range(4)}, f"issue {issue}")
        self.before_refactoring = _git(self.wt, "rev-parse", "HEAD")
        _commit(self.wt, {"src/i0/f0.py": "refactor 1\n"}, "refactor: 1")
        self.head = _commit(self.wt, {"src/i0/f0.py": "refactor 2\n"}, "refactor: 2")
        _git(self.wt, "push", "-q", "origin", "main")

    def add(self, files: dict[str, str]) -> str:
        sha = _commit(self.wt, files, "fix")
        _git(self.wt, "push", "-q", "origin", "main")
        return sha


@pytest.fixture()
def sprint(tmp_path) -> Sprint:
    return Sprint(tmp_path / "repo")


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(d))
    return d


def _state(sprint: Sprint, tmp_dir: pathlib.Path, **over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "tmp_dir": str(tmp_dir),
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": [],
        "deferred_nits": [],
        "final": None,
        "worktree_path": str(sprint.wt),
        "head_branch": "main",
        "review_kind": "code",
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def github(monkeypatch, sprint):
    """PR の REST の応答を差し替え、呼ばれた回数を数える。head はリポジトリの今の HEAD を返す。"""
    calls: list[int] = []
    base = {"sha": sprint.base}

    def _meta(pr, repo=None):
        calls.append(pr)
        head = _git(sprint.wt, "rev-parse", "HEAD")
        return review_lib.github.PrMetadata(REPO, "someone", "main", head, "develop", False, 4000, None, base_sha=base["sha"])

    monkeypatch.setattr(review_lib.github, "_fetch_pr_metadata", _meta)
    monkeypatch.setattr(review_lib.github, "_fetch_existing_comments", lambda repo, pr, path, strict=False: None)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda path: True)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    return {"calls": calls, "base": base}


def _start_round(capsys) -> dict[str, str]:
    capsys.readouterr()
    review_lib.commands.start_round.cmd_start_round(argparse.Namespace(pr=PR))
    out = capsys.readouterr().out
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _prompt(tmp_dir: pathlib.Path, rnd: int) -> str:
    bin_dir = tmp_dir / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    p = subprocess.run(
        ["bash", str(LAUNCH), "codex", str(PR), str(rnd)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "CROSS_REVIEW_TMP_DIR": str(tmp_dir)},
    )
    assert p.returncode == 0, p.stderr
    return (tmp_dir / f"codex-review-pr{PR}-prompt.md").read_text()


# ---------------- 範囲を求めてプロンプトに載せる ----------------


def test_sprint_pr_scope_counts_all_sixteen_files(sprint, tmp_dir, github, capsys):
    """AC1・AC9: 末尾の 2 コミット（1 ファイル）ではなく、base からの 16 ファイルを範囲にして渡す。"""
    _write(tmp_dir, _state(sprint, tmp_dir))
    out = _start_round(capsys)

    scope = _read(tmp_dir)["rounds"][-1]["review_scope"]
    assert scope == {"range": "full", "base_sha": sprint.base, "head_sha": sprint.head, "files": 16}
    assert out["REVIEW_SCOPE"] == "16"
    prompt = _prompt(tmp_dir, 1)
    assert f"base: {sprint.base}" in prompt
    assert f"head: {sprint.head}" in prompt
    assert f'git -C "{sprint.wt}" diff {sprint.base}...{sprint.head}' in prompt
    assert "差分のファイル数: 16" in prompt
    assert '"read_files": 16' in prompt
    # 担当がプロンプトのコマンドを打てば同じ数になる
    assert len(_git(sprint.wt, "diff", "--name-only", f"{sprint.base}...{sprint.head}").splitlines()) == 16


def test_round_two_uses_its_own_head_and_recounts(sprint, tmp_dir, github, capsys):
    """AC2: round 2 の head は round 2 の head で、ファイル数はその head で数え直す。round 1 の head は head として載らない。"""
    _write(tmp_dir, _state(sprint, tmp_dir))
    _start_round(capsys)
    st = _read(tmp_dir)
    st["rounds"][-1].update({"verdict": "scope_unmatched"})
    _write(tmp_dir, st)
    head_b = sprint.add({"src/new.py": "x\n"})

    _start_round(capsys)

    scope = _read(tmp_dir)["rounds"][-1]["review_scope"]
    assert scope["head_sha"] == head_b and scope["files"] == 17
    prompt = _prompt(tmp_dir, 2)
    assert f"head: {head_b}" in prompt and f"head: {sprint.head}" not in prompt
    assert "差分のファイル数: 17" in prompt
    # 範囲を求めても REST の呼び出しは 1 ラウンドに 1 回のまま（非機能の性能）
    assert len(github["calls"]) == 2


def test_design_round_two_scopes_from_previous_head(sprint, tmp_dir, github, capsys):
    """AC3: 設計 PR の 2 ラウンド目は、同じ PR の前のラウンドの head から今の head まで。"""
    _write(tmp_dir, _state(sprint, tmp_dir, review_kind="design"))
    _start_round(capsys)
    st = _read(tmp_dir)
    st["rounds"][-1].update({"verdict": "scope_unmatched"})
    _write(tmp_dir, st)
    head_b = sprint.add({"issues/x-design.md": "x\n", "src/i1/f1.py": "y\n"})

    _start_round(capsys)

    scope = _read(tmp_dir)["rounds"][-1]["review_scope"]
    assert scope == {"range": "since_previous", "base_sha": sprint.head, "head_sha": head_b, "files": 2}
    assert f"diff {sprint.head}...{head_b}" in _prompt(tmp_dir, 2)


def test_design_round_two_without_new_commits_falls_back_to_full(sprint, tmp_dir, github, capsys):
    """前の head と今の head が同じなら全範囲。"""
    _write(tmp_dir, _state(sprint, tmp_dir, review_kind="design"))
    _start_round(capsys)
    st = _read(tmp_dir)
    st["rounds"][-1].update({"verdict": "scope_unmatched"})
    _write(tmp_dir, st)
    _start_round(capsys)
    assert _read(tmp_dir)["rounds"][-1]["review_scope"]["range"] == "full"


def test_missing_base_launches_without_scope(sprint, tmp_dir, github, capsys):
    """AC8: base を取得しても取れなければ止まらず、範囲の節なしで起動し、判定は照合しない。"""
    github["base"]["sha"] = "d" * 40
    _write(tmp_dir, _state(sprint, tmp_dir))
    out = _start_round(capsys)

    assert _read(tmp_dir)["rounds"][-1]["review_scope"] == {"unavailable": "base_missing"}
    assert out["REVIEW_SCOPE"] == "unavailable:base_missing"
    prompt = _prompt(tmp_dir, 1)
    assert "## このラウンドのレビュー範囲" not in prompt and "read_files" not in prompt

    st = _read(tmp_dir)
    st["rounds"][-1].update({"reviewers": ["codex", "kiro"], "codex": _approve(None), "kiro": _approve(None)})
    _write(tmp_dir, st)
    code, jout = _judge(capsys)
    assert code == 0 and jout["SCOPE_CHECK"] == "unchecked:base_missing"


def test_base_fetched_when_missing_from_worktree(sprint, tmp_path):
    """base のコミットが worktree に無ければ origin から 1 度取得して数える。"""
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--single-branch", "--branch", "main", str(sprint.origin), str(clone)], check=True)
    # base のブランチが進んだ形。進んだ base は clone に無い
    _git(sprint.wt, "checkout", "-q", "-b", "develop", sprint.base)
    moved_base = _commit(sprint.wt, {"docs/other.md": "x\n"}, "base moved")
    _git(sprint.wt, "push", "-q", "origin", "develop")
    assert subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{moved_base}^{{commit}}"], capture_output=True).returncode != 0

    scope = review_lib.review_scope.compute(str(clone), sprint.head, moved_base, "code", 1, [], PR)
    assert scope["files"] == 16


def test_no_head_is_unavailable():
    assert review_lib.review_scope.compute("/nowhere", None, "a" * 40, "code", 1, [], PR) == {"unavailable": "no_head"}
    assert review_lib.review_scope.compute("/nowhere", "b" * 40, "", "code", 1, [], PR) == {"unavailable": "no_base"}


def test_pr_metadata_reads_base_sha():
    """base の SHA は head と同じ REST の応答から読む（決定 1）。"""
    body = {"number": PR, "head": {"ref": "h", "sha": "b" * 40, "repo": {"full_name": REPO}}, "base": {"ref": "main", "sha": "a" * 40}}
    meta = review_lib.github._pr_metadata_of(REPO, review_lib.github.RestResponse({}, body, None, None))
    assert meta is not None and meta.base_sha == "a" * 40


def test_legacy_round_launches_without_scope(sprint, tmp_dir):
    """移行性: `review_scope` の無いラウンドから起動しても止まらず、範囲の節を載せない。"""
    _write(tmp_dir, _state(sprint, tmp_dir, rounds=[{"round": 1, "pr": PR, "head_sha": sprint.head}]))
    assert "## このラウンドのレビュー範囲" not in _prompt(tmp_dir, 1)


def test_scope_for_another_head_is_not_shown(sprint, tmp_dir):
    """I1: 範囲の head がラウンドの head と違えば節を載せない。"""
    scope = {"range": "full", "base_sha": sprint.base, "head_sha": sprint.before_refactoring, "files": 16}
    _write(tmp_dir, _state(sprint, tmp_dir, rounds=[{"round": 1, "pr": PR, "head_sha": sprint.head, "review_scope": scope}]))
    assert "## このラウンドのレビュー範囲" not in _prompt(tmp_dir, 1)


# ---------------- 読んだファイル数と照合 ----------------


SCOPE16 = {"range": "full", "base_sha": "a" * 40, "head_sha": "b" * 40, "files": 16}


def _approve(read):
    entry = {"intent": "APPROVE", "by_severity": {}}
    if read is not None:
        entry["read_files"] = read
    else:
        entry.update({"read_files": None, "read_files_reason": "missing"})
    return entry


def _judge_state(tmp_dir: pathlib.Path, codex: dict, kiro: dict, **round_over) -> None:
    rnd = {"round": 1, "pr": PR, "started_at": "2026-10-03T00:00:00+09:00", "reviewers": ["codex", "kiro"]}
    rnd.update({"review_scope": SCOPE16, "codex": codex, "kiro": kiro})
    rnd.update(round_over)
    _write(
        tmp_dir,
        {"current_pr": PR, "repo": REPO, "tmp_dir": str(tmp_dir), "max_rounds": 12, "only": None, "rounds": [rnd], "final": None},
    )


def _judge(capsys) -> tuple[int, dict[str, str]]:
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        review_lib.commands.judge.cmd_judge(argparse.Namespace(pr=PR))
    out = capsys.readouterr().out
    return e.value.code, dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.fixture()
def ci_unverified(monkeypatch, state_mod):
    monkeypatch.setattr(
        review_lib.ci, "_round_ci", lambda st, last, pr: {"verdict": "unverified", "failed": [], "pending": [], "reason": "テスト"}
    )


@pytest.mark.parametrize("narrow", [1, 2])
def test_narrowed_approve_does_not_converge(tmp_dir, ci_unverified, capsys, narrow):
    """AC5・AC7・AC9: 16 ファイルの差分で 1・2 しか読まない APPROVE は数えず、修正を挟まずに次へ。"""
    _judge_state(tmp_dir, _approve(16), _approve(narrow))
    code, out = _judge(capsys)

    assert code == 2
    assert out["SCOPE_CHECK"] == "checked"
    assert out["SCOPE_UNMATCHED"] == f"'kiro={narrow}/16'"
    assert out["SCOPE_RETRY"] == "1"
    st = _read(tmp_dir)
    assert st["rounds"][-1]["verdict"] == "scope_unmatched" and st["final"] is None
    assert st["rounds"][-1]["scope_check"]["unmatched"] == [{"seat": "kiro", "read_files": narrow, "reason": "too_few"}]


def test_full_reads_converge(tmp_dir, ci_unverified, capsys):
    """AC5: 2 者とも 16 を読めば収束する。"""
    _judge_state(tmp_dir, _approve(16), _approve(16))
    code, out = _judge(capsys)
    assert code == 0 and out["SCOPE_UNMATCHED"] == "''" and "SCOPE_RETRY" not in out


def test_half_is_enough(tmp_dir, ci_unverified, capsys):
    """しきい値は半分（切り上げ）。16 なら 8 で数え、7 で外す。"""
    _judge_state(tmp_dir, _approve(8), _approve(7))
    code, out = _judge(capsys)
    assert code == 2 and out["SCOPE_UNMATCHED"] == "'kiro=7/16'"


@pytest.mark.parametrize(
    "raw, reason",
    [(None, "missing"), ("16", "not_integer"), (True, "not_integer"), (-1, "not_integer"), (16.0, "not_integer")],
)
def test_unmeasurable_read_files_are_not_counted(tmp_dir, ci_unverified, capsys, raw, reason):
    """AC4・AC6: 結果ファイルの `read_files` が無い・整数でないとき、取り込みは測れないと残し、判定は数えない。"""
    _write(
        tmp_dir,
        {
            "current_pr": PR,
            "repo": REPO,
            "tmp_dir": str(tmp_dir),
            "max_rounds": 12,
            "only": None,
            "final": None,
            "rounds": [
                {"round": 1, "pr": PR, "reviewers": ["codex", "kiro"], "review_scope": SCOPE16, "codex": _approve(16)},
            ],
        },
    )
    result = {"event": "APPROVE", "by_severity": {}}
    if raw is not None:
        result["read_files"] = raw
    rfile = tmp_dir / "kiro-result.json"
    rfile.write_text(json.dumps(result))
    review_lib.commands.read_result.cmd_read_result(argparse.Namespace(pr=PR, agent="kiro", file=str(rfile)))

    entry = _read(tmp_dir)["rounds"][-1]["kiro"]
    assert entry["read_files"] is None and entry["read_files_reason"] == reason
    code, out = _judge(capsys)
    assert code == 2 and out["SCOPE_UNMATCHED"] == f"'kiro={reason}/16'" and out["SCOPE_RETRY"] == "1"


def test_read_files_recorded(tmp_dir, capsys):
    """AC4: 整数の `read_files` は席の記録に同じ値で残る。"""
    _judge_state(tmp_dir, _approve(16), {})
    rfile = tmp_dir / "kiro-result.json"
    rfile.write_text(json.dumps({"event": "APPROVE", "by_severity": {}, "read_files": 12}))
    review_lib.commands.read_result.cmd_read_result(argparse.Namespace(pr=PR, agent="kiro", file=str(rfile)))
    entry = _read(tmp_dir)["rounds"][-1]["kiro"]
    assert entry["read_files"] == 12 and "read_files_reason" not in entry


def test_request_changes_findings_kept_regardless_of_read_files(tmp_dir, capsys):
    """AC10・I3: 1 しか読まない REQUEST_CHANGES の指摘も取り込み、判定は修正へ回す。"""
    _judge_state(tmp_dir, _approve(16), {})
    payload = {
        "summary": "s",
        "comments": [
            {"path": "src/i0/f0.py", "line": 1, "body": "[major / 正確性] a", "severity": "major", "evidence": "e", "falsification": "f"},
            {"path": "src/i0/f0.py", "line": 1, "body": "[major / 正確性] b", "severity": "major", "evidence": "e", "falsification": "f"},
        ],
    }
    (tmp_dir / f"kiro-review-pr{PR}-round1-payload.json").write_text(json.dumps(payload))
    rfile = tmp_dir / "kiro-result.json"
    rfile.write_text(json.dumps({"event": "REQUEST_CHANGES", "by_severity": {"major": 2}, "read_files": 1}))
    review_lib.commands.read_result.cmd_read_result(argparse.Namespace(pr=PR, agent="kiro", file=str(rfile)))

    assert len([f for f in _read(tmp_dir)["review_findings"] if f["agent"] == "kiro"]) == 2
    code, out = _judge(capsys)
    assert code == 2 and "SCOPE_RETRY" not in out and out["SCOPE_UNMATCHED"] == "''"
    assert _read(tmp_dir)["rounds"][-1]["verdict"] == "changes_requested"


def test_model_stage_is_not_checked(tmp_dir, capsys):
    """決定 5: モデルの段は照合せず、今どおり model_confirmed。"""
    _judge_state(tmp_dir, _approve(1), _approve(1), stage="model")
    code, out = _judge(capsys)
    assert code == 2 and out["SCOPE_CHECK"] == "unchecked:model_stage" and out.get("MODEL_CONFIRMED") == "1"


def test_legacy_round_is_not_checked(tmp_dir, ci_unverified, capsys):
    """移行性: `review_scope` の無いラウンドは照合せず、今と同じ出口。"""
    _judge_state(tmp_dir, {"intent": "APPROVE", "by_severity": {}}, {"intent": "APPROVE", "by_severity": {}})
    st = _read(tmp_dir)
    del st["rounds"][-1]["review_scope"]
    _write(tmp_dir, st)
    code, out = _judge(capsys)
    assert code == 0 and out["SCOPE_CHECK"] == "unchecked:legacy"


# ---------------- 次のラウンドへ ----------------


def test_next_round_opens_without_fix_and_max_rounds_still_applies(sprint, tmp_dir, github, capsys):
    """AC7・I5: 照合で外れたラウンドの後は修正の記録なしで次を開き、上限は今のまま効く。"""
    _write(tmp_dir, _state(sprint, tmp_dir, max_rounds=2))
    for _ in range(2):
        _start_round(capsys)
        st = _read(tmp_dir)
        st["rounds"][-1]["verdict"] = "scope_unmatched"
        _write(tmp_dir, st)
    with pytest.raises(SystemExit) as e:
        _start_round(capsys)
    assert e.value.code == 1 and _read(tmp_dir)["final"] == "max_rounds"


def test_drive_goes_to_next_round_without_fix():
    """I5: 駆動は SCOPE_RETRY=1 を修正へ回さずに次のラウンドへ進める。"""
    import test_review_drive_resume as resume

    d = resume.cr.Drive(PR, "light", [])
    assert d.after_judge(2, "SCOPE_RETRY=1\n") == "round"
