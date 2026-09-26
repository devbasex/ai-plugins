"""再開で渡した引数を状態ファイルへ反映する（#727 / #648 の AC25〜AC29）。

**黙って捨てる引数を残さない**（設計の決定 13）。状態ファイルに載る引数は、反映の表の
「反映する」か「知らせる」のどちらかに必ず載る。担当に関わる引数（`--only` /
`--include` / `--exclude` / `--require-all`）を渡した再開だけが、使える者の解決を
やり直して参加者を作り直す（決定 14）。渡さなかった引数は状態ファイルの値で補う。
"""
from __future__ import annotations

import json
import pathlib

import pytest
import review_lib
import review_lib.commands.init
import review_lib.commands.report
import review_lib.commands.start_round
import review_lib.findings
import review_lib.github
import review_lib.participants
import review_lib.posts
import review_lib.workspace

PR = 6100
REPO = "acme/demo"


def _participants(**over) -> dict:
    p = {
        "pool": ["codex", "agy", "kiro"],
        "included": [], "excluded": [],
        "available": ["codex", "agy", "kiro"],
        "unavailable": {}, "probe_skipped": False, "require_all": False,
        "fallback": [],
    }
    p.update(over)
    return p


def _state(tmp_dir: pathlib.Path, **over) -> pathlib.Path:
    st = {
        "started_at": "2026-09-19T00:00:00+09:00",
        "host": "claude",
        "host_source": "explicit",
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "participants": _participants(),
        "resume_changes": [],
        "current_pr": PR,
        "worktree_path": str(tmp_dir),
        "tmp_dir": str(tmp_dir),
        "repo": REPO,
        "head_branch": "feat/x",
        "base_branch": "develop",
        "auto_review_instructions": "",
        "review_instructions": "",
        "verify_commands": [],
        "verify_exit_codes": [],
        "pr_history": [{"pr": PR, "opened_at": "x", "closed_at": None, "rounds": 0}],
        "rounds": [],
        "deferred_nits": [],
        "carried_over": None,
        "final": None,
    }
    st.update(over)
    path = tmp_dir / f"cross-review-pr{PR}-state.json"
    path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture()
def resume(state_mod, monkeypatch, tmp_path):
    """再開の入口を、GitHub にも git にも触れずに通す。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: REPO)
    monkeypatch.setattr(review_lib.posts, "_auto_flush", lambda pr: None)
    monkeypatch.setattr(review_lib.findings, "_record_carried_over", lambda *a, **k: False)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda path: False)
    monkeypatch.setattr(review_lib.github, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(review_lib.commands.start_round, "_sync_before_round", lambda st, pr: None)
    calls: list[list[str]] = []

    def probe(runtimes, *, info, env=None):
        calls.append(list(runtimes))
        return ({r: {"command": r, "ok": True, "detail": ""} for r in runtimes}, False)

    monkeypatch.setattr(review_lib.participants.auth, "probe_auth", probe)

    def run(*argv: str) -> dict:
        args = state_mod.build_parser().parse_args(
            ["init", str(PR), "--worktree", str(tmp_path), *argv])
        review_lib.commands.init.cmd_init(args)
        return json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())

    run.calls = calls
    run.tmp_path = tmp_path
    return run


def _seats(state_mod, tmp_path) -> list[str]:
    review_lib.commands.start_round.cmd_start_round(type("A", (), {"pr": PR})())
    st = json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())
    return st["rounds"][-1]["reviewers"]


# ---------------- 反映する引数（AC25） ----------------

def test_max_rounds_is_replaced_and_recorded(resume, tmp_path, capsys):
    """AC25: `--max-rounds 20` は状態へ反映され、1 行出て、記録へ 1 件積まれる。"""
    _state(tmp_path)
    st = resume("--max-rounds", "20")
    assert st["max_rounds"] == 20
    assert "↻ max_rounds: 12 → 20" in capsys.readouterr().err
    changes = [c for c in st["resume_changes"] if c["field"] == "max_rounds"]
    assert len(changes) == 1
    assert changes[0]["from"] == 12 and changes[0]["to"] == 20
    assert changes[0]["at"]


def test_the_other_replaced_fields_are_applied_too(resume, tmp_path):
    """AC25 後半: `--rotate-after` / `--verify-command` / `--verify-exit-code` も反映する。"""
    _state(tmp_path, verify_commands=["pytest -q"], verify_exit_codes=[1])
    st = resume("--rotate-after", "4", "--verify-command", "ruff check",
                "--verify-exit-code", "2")
    assert st["rotate_after"] == 4
    # 置き換えであり、足し込みではない。
    assert st["verify_commands"] == ["ruff check"]
    assert st["verify_exit_codes"] == [2]


def test_the_same_value_is_not_recorded(resume, tmp_path, capsys):
    """同じ値を渡した再開は、行も記録も出さない。"""
    _state(tmp_path)
    st = resume("--max-rounds", "12")
    assert st["resume_changes"] == []
    assert "max_rounds" not in capsys.readouterr().err


# ---------------- 渡さない再開（AC26） ----------------

def test_a_resume_without_arguments_changes_nothing(resume, tmp_path):
    """AC26: 引数を渡さない再開では 6 項目が変わらず、確認コマンドは 1 回も呼ばれない。"""
    _state(tmp_path, only="kiro", verify_commands=["pytest -q"], verify_exit_codes=[1],
           participants=_participants(available=["codex", "kiro"]))
    before = json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text())
    st = resume()
    for key in ("max_rounds", "rotate_after", "verify_commands", "verify_exit_codes",
                "only", "participants"):
        assert st[key] == before[key], key
    assert resume.calls == []
    assert st["resume_changes"] == []


# ---------------- 1 者指定（AC27） ----------------

def test_only_is_replaced_and_narrows_the_next_round(resume, state_mod, tmp_path):
    """AC27: `--only codex` は `only` を書き換え、次のラウンドを 1 席にする。"""
    _state(tmp_path, rounds=[{"round": 1, "pr": PR, "started_at": "x",
                              "reviewers": ["agy", "kiro"], "verdict": "approved",
                              "agy": {"intent": "APPROVE", "by_severity": {}},
                              "kiro": {"intent": "APPROVE", "by_severity": {}}}])
    st = resume("--only", "codex")
    assert st["only"] == "codex"
    # 過去のラウンドの担当は変わらない（決定 11）。
    assert st["rounds"][0]["reviewers"] == ["agy", "kiro"]
    assert _seats(state_mod, tmp_path) == ["codex"]


def test_only_that_cannot_be_reached_stops_before_writing(
        resume, state_mod, tmp_path, monkeypatch, capsys):
    """再開で渡した 1 者指定が確認を通らなければ、状態を書き換えずに終了コード 1。

    1 者指定は状態へ反映する引数であると同時に、参加者を作り直す引数でもある。
    作り直しが 0 者になったまま先へ進むと、確認を通らない 1 者が次のラウンドの席に座る。
    """
    path = _state(tmp_path)
    before = path.read_text(encoding="utf-8")

    def probe(runtimes, *, info, env=None):
        return ({r: {"command": r, "ok": False, "detail": "未認証"} for r in runtimes}, False)

    monkeypatch.setattr(review_lib.participants.auth, "probe_auth", probe)
    with pytest.raises(SystemExit) as e:
        resume("--only", "kiro")
    assert e.value.code == 1
    assert path.read_text(encoding="utf-8") == before
    assert "1 者指定の kiro が確認を通りません" in capsys.readouterr().err


def test_only_none_clears_the_narrowing(resume, tmp_path, capsys):
    """AC27 後半: `--only none` は `only` を `null` へ戻す（決定 15）。"""
    _state(tmp_path, only="codex")
    st = resume("--only", "none")
    assert st["only"] is None
    assert "↻ only: codex → None" in capsys.readouterr().err
    assert [c["field"] for c in st["resume_changes"]].count("only") == 1


# ---------------- 外す者・足す者（AC28） ----------------

def test_exclude_reruns_the_probe_and_drops_the_name(resume, state_mod, tmp_path):
    """AC28: `--exclude kiro` は確認をやり直し、使える者から kiro を外す。

    作り直しは保存された `pool` ではなく今の母集合（claude / codex / kiro とホスト）から解決する。
    """
    _state(tmp_path)
    st = resume("--exclude", "kiro")
    assert resume.calls == [["claude", "codex"]]
    assert st["participants"]["excluded"] == ["kiro"]
    assert st["participants"]["available"] == ["claude", "codex"]
    assert "kiro" not in _seats(state_mod, tmp_path)


def test_exclude_agy_on_resume_is_ignored(resume, tmp_path):
    """#786 の AC4: 既定の母集合に無い agy の除外は `ignored_exclude` に残り、`excluded` は空。"""
    _state(tmp_path)
    st = resume("--exclude", "agy")
    assert st["participants"]["excluded"] == []
    assert st["participants"]["ignored_exclude"] == ["agy"]
    assert st["participants"]["available"] == ["claude", "codex", "kiro"]


def test_the_participants_are_recorded_as_one_change(resume, tmp_path):
    """決定 16: 参加者の作り直しは、項目ごとではなく 1 件として積む。"""
    _state(tmp_path)
    st = resume("--exclude", "kiro")
    changes = [c for c in st["resume_changes"] if c["field"] == "participants"]
    assert len(changes) == 1
    assert changes[0]["from"]["excluded"] == []
    assert changes[0]["to"]["excluded"] == ["kiro"]


def test_exclude_none_clears_the_exclusions(resume, tmp_path):
    """AC28: `--exclude none` は外す者を空へ戻す。"""
    _state(tmp_path, participants=_participants(excluded=["kiro"], available=["codex"]))
    st = resume("--exclude", "none")
    assert st["participants"]["excluded"] == []
    assert st["participants"]["available"] == ["claude", "codex", "kiro"]


def test_unpassed_arguments_come_from_the_state_file(resume, tmp_path):
    """AC28 後半: 渡さなかった引数は状態ファイルの値で補う（決定 14）。"""
    _state(tmp_path, participants=_participants(
        included=["claude"], available=["claude", "codex", "kiro"]))
    st = resume("--exclude", "kiro")
    assert st["participants"]["included"] == ["claude"]
    assert st["participants"]["excluded"] == ["kiro"]
    assert st["participants"]["available"] == ["claude", "codex"]


def _started_with_exclude_agy(tmp_path):
    return _state(tmp_path, participants=_participants(
        pool=["claude", "codex", "kiro"], excluded=[], ignored_exclude=["agy"],
        available=["claude", "codex", "kiro"]))


def test_ignored_exclusions_survive_a_resume_without_exclude(resume, state_mod, tmp_path, capsys):
    """#786 の AC4d: `--exclude agy` で始めた実行を `--include codex` だけで再開しても残り、報告に出る。"""
    _started_with_exclude_agy(tmp_path)
    st = resume("--include", "codex")
    assert st["participants"]["ignored_exclude"] == ["agy"]
    assert st["participants"]["excluded"] == []
    st["final"] = "approved"
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(st, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    review_lib.commands.report.cmd_report(type("A", (), {"pr": PR})())
    assert "- --exclude で指定したが既定の母集合に無かった者: agy" in capsys.readouterr().out


def test_real_and_ignored_exclusions_survive_a_resume_without_exclude(resume, tmp_path):
    """現状固定: 実際の除外と母集合外の除外を同時に持つ状態も再開できる。"""
    _state(tmp_path, participants=_participants(
        pool=["claude", "codex", "kiro"], excluded=["kiro"],
        ignored_exclude=["agy"], available=["claude", "codex"]))

    st = resume("--require-all")

    assert st["participants"]["excluded"] == ["kiro"]
    assert st["participants"]["ignored_exclude"] == ["agy"]
    assert st["participants"]["available"] == ["claude", "codex"]
    assert resume.calls == [["claude", "codex"]]


def test_include_wins_over_an_ignored_exclusion(resume, tmp_path):
    """#786 の AC4d: 無視した除外の名前を `--include` で渡すと、足し戻さずに参加者へ戻す。"""
    _started_with_exclude_agy(tmp_path)
    st = resume("--include", "agy")
    assert st["participants"]["ignored_exclude"] == []
    assert st["participants"]["available"] == ["claude", "codex", "agy", "kiro"]


def test_only_wins_over_an_ignored_exclusion(resume, state_mod, tmp_path):
    """#786 の決定 12: `--exclude agy` で始めた実行を `--only agy` で再開すると、agy 1 者で回る。

    再開時の 1 者指定も新しい指定であり、無視した除外を足し戻して矛盾で止めない。
    """
    _started_with_exclude_agy(tmp_path)
    st = resume("--only", "agy")
    assert st["only"] == "agy"
    assert st["participants"]["ignored_exclude"] == []
    assert st["participants"]["available"] == ["agy"]
    assert _seats(state_mod, tmp_path) == ["agy"]


def test_include_of_a_real_exclusion_still_conflicts(resume, tmp_path):
    """外した者（`excluded`）と `--include` の重なりは今どおり止める。"""
    path = _state(tmp_path, participants=_participants(
        pool=["claude", "codex", "kiro"], excluded=["kiro"], available=["claude", "codex"]))
    before = path.read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        resume("--include", "kiro")
    assert e.value.code == 1
    assert path.read_text(encoding="utf-8") == before


def test_only_none_after_only_agy_returns_to_the_default_three(resume, state_mod, tmp_path):
    """#786 の AC4b: `--only agy` で始めた実行を `--only none` で再開すると、既定の 3 者へ戻る。"""
    _state(tmp_path, only="agy", participants=_participants(
        pool=["claude", "codex", "kiro"], available=["agy"]))
    st = resume("--only", "none")
    assert st["only"] is None
    assert st["participants"]["available"] == ["claude", "codex", "kiro"]
    assert "agy" not in _seats(state_mod, tmp_path)


def test_require_all_alone_rebuilds_the_participants(resume, tmp_path):
    """`--require-all` だけでも作り直す（担当に関わる引数のため）。"""
    _state(tmp_path)
    st = resume("--require-all")
    assert st["participants"]["require_all"] is True
    assert resume.calls == [["claude", "codex", "kiro"]]


def test_a_failed_rebuild_leaves_the_state_untouched(resume, state_mod, tmp_path, monkeypatch):
    """作り直しが失敗したら、状態ファイルを書き換えずに終了コード 1 で終わる。"""
    path = _state(tmp_path)
    before = path.read_text(encoding="utf-8")

    def probe(runtimes, *, info, env=None):
        return ({r: {"command": r, "ok": False, "detail": "未認証"} for r in runtimes}, False)

    monkeypatch.setattr(review_lib.participants.auth, "probe_auth", probe)
    with pytest.raises(SystemExit) as e:
        resume("--exclude", "agy", "--require-all")
    assert e.value.code == 1
    assert path.read_text(encoding="utf-8") == before


def test_a_state_without_participants_can_be_rebuilt(resume, tmp_path):
    """`participants` を持たない状態ファイル（`host` だけ）でも作り直せる。"""
    path = _state(tmp_path)
    st = json.loads(path.read_text(encoding="utf-8"))
    del st["participants"]
    path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    saved = resume("--exclude", "agy")
    assert saved["participants"]["available"] == ["claude", "codex", "kiro"]
    changes = [c for c in saved["resume_changes"] if c["field"] == "participants"]
    assert changes[0]["from"] is None


# ---------------- 知らせる引数（AC29） ----------------

def test_host_is_not_applied_but_reported(resume, tmp_path, capsys):
    """AC29: `--host codex` は反映せず、1 行で知らせる。"""
    _state(tmp_path)
    st = resume("--host", "codex")
    assert st["host"] == "claude"
    err = capsys.readouterr().err
    assert err.count("ℹ --host は再開では反映しません（状態: claude / 指定: codex）") == 1
    assert st["resume_changes"] == []


def test_the_same_host_prints_nothing(resume, tmp_path, capsys):
    """AC29 後半: 状態と同じ `--host claude` では何も出さない。"""
    _state(tmp_path)
    resume("--host", "claude")
    assert "--host" not in capsys.readouterr().err
