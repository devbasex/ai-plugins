"""レビュワーの母集合と終了基準（#371）。

**母集合は claude / codex / kiro とホストである（#892 でホストを含め、#786 で agy を既定から
外した。agy は `--include agy` で足し、ホストが agy なら 4 者）。** レビュー担当は CLI プロセス
として起動するため、ホストと同じランタイムでもホストの会話の作業文脈は持ち込まれない。担当はラウンドごとの
輪番で 2 者を選ぶ。`participants` を持たず `host` だけを持つ古い状態ファイルは、変更の前の
母集合（全ランタイム − ホスト）で輪番を回す。

**終了基準は「新しい指摘が出ない」である。** 全員 `APPROVE` は、最も止まらない参加者に
律速される。同じ論点の再提出では止まる。
"""
from __future__ import annotations

import json
import subprocess
import types

import pytest

# 子プロセスの起動の本物。テストのフィクスチャが差し替える前に控える（#813 の AC10 で
# 認証の確認だけを本物のまま走らせるため）。
_REAL_RUN = subprocess.run


def _state(tmp_path, **over):
    """最小の状態ファイルを組み立ててパスを返す。"""
    st = {
        "started_at": "2026-09-04T00:00:00",
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "current_pr": 500,
        "worktree_path": str(tmp_path),
        "tmp_dir": str(tmp_path),
        "repo": "acme/demo",
        "head_branch": "feat/x",
        "base_branch": "develop",
        "host": "claude",
        "host_source": "explicit",
        "pr_history": [{"pr": 500, "opened_at": "x", "closed_at": None, "rounds": 0}],
        "rounds": [],
        "deferred_nits": [],
        "carried_over": None,
        "final": None,
    }
    st.update(over)
    path = tmp_path / "cross-review-pr500-state.json"
    path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return path


def _round(round_no, reviewers, verdicts, pr=500):
    """1 ラウンド分の記録。`verdicts` は `{担当: intent}`。"""
    entry = {
        "round": round_no, "pr": pr, "reviewers": list(reviewers),
        "started_at": "2026-09-04T00:00:00",
    }
    for name, intent in verdicts.items():
        entry[name] = {"intent": intent, "posted_as": "COMMENT", "comments": 0,
                       "review_url": "https://x/pull/500#pullrequestreview-1",
                       "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0}}
    return entry


@pytest.fixture(autouse=True)
def _no_external(state_mod, monkeypatch, tmp_path):
    """外部への問い合わせを止める。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(state_mod, "_auto_flush", lambda pr: None)
    monkeypatch.setattr(state_mod, "_pending_posts", lambda pr: 0)
    monkeypatch.setattr(state_mod, "_record_carried_over", lambda *a, **k: False)
    monkeypatch.setattr(state_mod, "_round_ci", lambda *a, **k: {"verdict": "success",
                                                             "failed": [], "pending": [],
                                                             "note": "", "reason": ""})


def _payload(tmp_path, agent, pr, round_no, comments):
    """レビューの指摘の記録（振動検知と新規性の判定が読む）。"""
    p = tmp_path / f"{agent}-review-pr{pr}-round{round_no}-payload.json"
    p.write_text(json.dumps({"comments": comments}, ensure_ascii=False), encoding="utf-8")
    return p


def _comment(path="src/a.py", line=10, body="ここを直す"):
    return {"path": path, "line": line, "body": body}


# ---------- 母集合 ----------

def test_a_host_only_state_keeps_reviewers_other_than_the_host(state_mod, tmp_path):
    """`host` だけを持つ古い状態ファイルでは、担当はホストを含まない 2 者のままである。"""
    for host in ("claude", "codex", "agy", "kiro"):
        path = _state(tmp_path, host=host)
        st = json.loads(path.read_text(encoding="utf-8"))
        picked = state_mod._round_reviewers(st, 1)
        assert len(picked) == 2
        assert host not in picked


def test_state_without_a_host_keeps_the_two_named_reviewers(state_mod, tmp_path):
    """`host` を持たない状態ファイルは、これまでの 2 者を担当として読む。

    中断した実行を新しい版で再開したときに、担当が入れ替わって前のラウンドの記録と
    突き合わせられなくなることを避ける。
    """
    path = _state(tmp_path)
    st = json.loads(path.read_text(encoding="utf-8"))
    del st["host"]
    assert state_mod._round_reviewers(st, 1) == ["codex", "agy"]


def test_recorded_reviewers_win_over_the_rotation(state_mod, tmp_path):
    """ラウンドに記録された担当があれば、それを使う。"""
    path = _state(tmp_path, rounds=[_round(1, ["kiro", "codex"], {})])
    st = json.loads(path.read_text(encoding="utf-8"))
    assert state_mod._round_reviewers(st, 1) == ["kiro", "codex"]


# ---------- 終了基準: 新規の指摘 ----------

def test_no_findings_converges_on_the_first_round(state_mod, tmp_path, capsys):
    """指摘が 0 件のラウンドは、初回でも収束する。

    **指摘の記録が無いことと、指摘が 0 件であることは区別できない。** どちらも読める
    指摘が 0 件になるため、新規性は測れないものとして扱い（出力は `-`）、従来どおり
    全員が pass かどうかで収束を決める。
    """
    _state(tmp_path, rounds=[_round(1, ["codex", "agy"],
                                    {"codex": "APPROVE", "agy": "APPROVE"})])
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 0
    assert "NEW_FINDINGS=-" in capsys.readouterr().out


def test_only_repeated_findings_converge(state_mod, tmp_path, capsys):
    """前のラウンドと同じ指摘だけが残ったラウンドは収束する。

    全員 `APPROVE` は最も止まらない参加者に律速される。同じ論点の再提出では止まる。
    """
    _state(tmp_path, rounds=[
        _round(1, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
        _round(2, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
    ])
    _payload(tmp_path, "agy", 500, 1, [_comment()])
    _payload(tmp_path, "agy", 500, 2, [_comment()])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 0
    assert "NEW_FINDINGS=0" in capsys.readouterr().out


def test_a_new_finding_keeps_the_loop_running(state_mod, tmp_path, capsys):
    """新しい観点が出ているあいだは回る。"""
    _state(tmp_path, rounds=[
        _round(1, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
        _round(2, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
    ])
    _payload(tmp_path, "agy", 500, 1, [_comment()])
    _payload(tmp_path, "agy", 500, 2, [_comment(), _comment("src/b.py", 99, "別の指摘")])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 2
    assert "NEW_FINDINGS=1" in capsys.readouterr().out


def test_carried_over_findings_win_over_the_new_finding_count(state_mod, tmp_path, monkeypatch):
    """引き継いだ指摘があるラウンドは、新規 0 件でも修正へ回る。"""
    _state(tmp_path,
           carried_over={"count": 2, "thread_ids": ["a", "b"], "fixed_in_round": None},
           rounds=[_round(1, ["codex", "agy"],
                          {"codex": "APPROVE", "agy": "APPROVE"})])
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 2


def test_judge_prints_intents_by_reviewer_name(state_mod, tmp_path, capsys):
    """判定の出力は担当名を含む 1 変数で返す。担当は 4 つの名前を取りうる。"""
    _state(tmp_path, host="codex",
           rounds=[_round(1, ["claude", "kiro"],
                          {"claude": "APPROVE", "kiro": "APPROVE"})])
    with pytest.raises(SystemExit):
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    out = capsys.readouterr().out
    assert "REVIEWER_INTENTS='claude=APPROVE kiro=APPROVE'" in out


# ---------- 振動検知との順序 ----------

def test_a_fully_repeated_round_converges_instead_of_oscillating(state_mod, tmp_path):
    """新規 0 件のラウンドは重複率 1.0 になる。**収束を先に見る。**

    順序を逆にすると、収束すべきラウンドが中断として落ちる。
    """
    _state(tmp_path, rounds=[
        _round(1, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
        _round(2, ["codex", "agy"], {"codex": "APPROVE", "agy": "REQUEST_CHANGES"}),
    ])
    _payload(tmp_path, "agy", 500, 1, [_comment()])
    _payload(tmp_path, "agy", 500, 2, [_comment()])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 0


# ---------- init / start-round ----------

def test_start_round_records_the_reviewers(state_mod, tmp_path, capsys, monkeypatch):
    """ラウンドを開くときに担当を決めて残す。後から引き直すと記録とずれる。"""
    path = _state(tmp_path, host="codex")
    monkeypatch.setattr(state_mod, "_sync_before_round", lambda st, pr: None)
    state_mod.cmd_start_round(type("A", (), {"pr": 500})())
    st = json.loads(path.read_text(encoding="utf-8"))
    # ホスト codex の母集合は claude / agy / kiro。ラウンド 1 は先頭の claude を外す
    assert st["rounds"][-1]["reviewers"] == ["agy", "kiro"]
    out = capsys.readouterr().out
    assert "REVIEWERS='agy kiro'" in out
    assert "REVIEWERS_CSV=agy,kiro" in out


def test_read_result_accepts_every_runtime(state_mod):
    """`read-result` は担当になりうる 4 つの名前（`ALL_RUNTIMES`）すべてを受け取る。

    実機で `kiro` が結果を書いたのに `invalid choice` で弾かれた。担当が 4 つの名前を
    取りうる以上（agy も `--include agy` かホストが agy なら座る）、副コマンドの引数も
    同じ名前の集合を持たなければ、結果を残した担当が
    「結果なし」として扱われる。
    """
    parser = state_mod.build_parser()
    for runtime in ("codex", "agy", "claude", "kiro"):
        args = parser.parse_args(["read-result", "500", runtime])
        assert args.agent == runtime


# ---------- --only と母集合の相互作用 ----------

def test_only_narrows_the_round_reviewers(state_mod, tmp_path):
    """`--only` を指定したラウンドの担当は、その 1 者だけになる。

    輪番が返す 2 者を担当のまま残すと、`--only` で絞った側が 1 者も起動されない
    ラウンドが生まれる。そのとき全員が「指定によるスキップ」として扱われ、**誰も
    レビューしていないのに収束する。**
    """
    path = _state(tmp_path, only="kiro")
    st = json.loads(path.read_text(encoding="utf-8"))
    assert state_mod._round_reviewers(st, 1) == ["kiro"]


def test_init_accepts_the_host_as_only(state_mod, tmp_path, monkeypatch):
    """母集合がホストを含むため、`--only <ホスト>` も受け付ける（#892）。

    母集合の外を指せるのは外した者だけで、その矛盾は
    `test_contradicting_names_fail_before_the_state_is_written` が確かめる。
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(state_mod.auth, "probe_auth", _fake_probe({}, calls))
    p = state_mod._resolve_reviewers("claude", _init_args(tmp_path, only="claude"))
    assert p["available"] == ["claude"]
    p = state_mod._resolve_reviewers("claude", _init_args(tmp_path, only="codex"))
    assert p["available"] == ["codex"]
    p = state_mod._resolve_reviewers("claude", _init_args(tmp_path))
    assert p["available"] == ["claude", "codex", "kiro"]


def test_judge_returns_the_relaunch_targets_as_a_list(state_mod, tmp_path, capsys):
    """起動し直す担当は名前の一覧で返す。`both` は 2 者だけを指す語である。"""
    _state(tmp_path, host="codex",
           rounds=[_round(1, ["claude", "kiro"], {"claude": "APPROVE"})])
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(type("A", (), {"pr": 500})())
    assert e.value.code == 7
    out = capsys.readouterr().out
    assert "RELAUNCH_AGENTS='kiro'" in out
    assert "RELAUNCH_AGENTS_CSV=kiro" in out


def test_auth_check_covers_only_the_reviewers_that_run(state_mod, tmp_path, monkeypatch):
    """`--only` を指定したときは、実際に起動する 1 者だけを確かめる。

    母集合の全員を確かめると、そのラウンドで起動しない CLI の未認証で `init` が
    失敗する。デバッグのために 1 者へ絞った意味が無くなる。1 者指定は埋め合わせを
    しないため、ホストも確かめない（AC18 後半）。
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(state_mod.auth, "probe_auth", _fake_probe({}, calls))
    state_mod._resolve_reviewers("claude", _init_args(tmp_path, only="kiro"))
    assert calls == [["kiro"]]
    calls.clear()
    state_mod._resolve_reviewers("claude", _init_args(tmp_path))
    assert calls == [["claude", "codex", "kiro"]]


def test_init_fails_when_the_host_cannot_be_guessed(state_mod, monkeypatch):
    """手掛かりが無ければ、既定を置かずに失敗する。

    誤ったホストが状態ファイルと出力に残る。間違ったまま一周してしまい、
    成果物を見るまで気付けない。
    """
    for key, _ in state_mod.assignment.HOST_ENV_HINTS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(state_mod.assignment.AssignmentError):
        state_mod.assignment.detect_host(None, env={})


def test_report_shows_every_reviewer_that_took_part(state_mod, tmp_path, capsys):
    """完了報告は、実際に参加した担当の判定を出す。

    2 者を固定した表のままだと、`claude` / `kiro` が担当したラウンドの結果が読めない。
    **利用者が結果を確認できないまま収束する。**
    """
    _state(tmp_path, host="codex", final="approved", rounds=[
        _round(1, ["claude", "kiro"], {"claude": "APPROVE", "kiro": "REQUEST_CHANGES"}),
        _round(2, ["agy", "claude"], {"agy": "APPROVE", "claude": "APPROVE"}),
    ])
    state_mod.cmd_report(type("A", (), {"pr": 500})())
    out = capsys.readouterr().out
    assert "claude=APPROVE" in out
    assert "kiro=REQUEST_CHANGES" in out
    assert "agy=APPROVE" in out


# ---------- 使える者の解決と新規の初期化（#727: AC14〜AC20） ----------

PR_INIT = 500
REPO_INIT = "acme/demo"


def _fake_probe(failing: dict[str, str], calls: list[list[str]], skipped: bool = False):
    """止めない確認の差し替え。`failing` の名前だけ通らず、理由を `detail` に入れる。"""
    def probe(runtimes, *, info, env=None):
        calls.append(list(runtimes))
        if skipped:
            return {}, True
        return ({r: {"command": r, "ok": r not in failing, "detail": failing.get(r, "")}
                 for r in runtimes}, False)
    return probe


def _init_args(tmp_path, *argv: str, only=None):
    """`init` の引数を、実際の入口（`build_parser`）と同じ形で組む。"""
    words = ["init", str(PR_INIT), "--host", "claude", "--worktree", str(tmp_path / "wt")]
    if only is not None:
        words += ["--only", only]
    words += list(argv)
    return state_mod_parser().parse_args(words)


_PARSER = {}


def state_mod_parser():
    return _PARSER["p"]


@pytest.fixture(autouse=True)
def _parser(state_mod):
    _PARSER["p"] = state_mod.build_parser()


@pytest.fixture()
def new_init(state_mod, monkeypatch, tmp_path):
    """新規の初期化を GitHub と git に触れずに通す。"""
    (tmp_path / "wt").mkdir(exist_ok=True)
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO_INIT)
    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", lambda pr, repo=None:
                        state_mod.PrMetadata(REPO_INIT, "author", "feat/x", "abc",
                                             "develop", False, 4000, None))
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "viewer")
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda path: True)
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(state_mod.subprocess, "run", lambda *a, **k:
                        __import__("subprocess").CompletedProcess(a[0], 0, stdout="", stderr=""))
    monkeypatch.setattr(state_mod, "_sync_before_round", lambda st, pr: None)
    calls: list[list[str]] = []

    def run(*argv: str, failing=None, only=None, real_probe=False):
        if not real_probe:
            monkeypatch.setattr(state_mod.auth, "probe_auth", _fake_probe(failing or {}, calls))
        state_mod.cmd_init(_init_args(tmp_path, *argv, only=only))
        return json.loads((tmp_path / f"cross-review-pr{PR_INIT}-state.json").read_text())

    run.calls = calls
    run.state_file = tmp_path / f"cross-review-pr{PR_INIT}-state.json"
    return run


def _start_round(state_mod, tmp_path):
    state_mod.cmd_start_round(type("A", (), {"pr": PR_INIT})())
    st = json.loads((tmp_path / f"cross-review-pr{PR_INIT}-state.json").read_text())
    return st["rounds"][-1]["reviewers"]


def test_a_failing_reviewer_is_dropped_and_init_still_succeeds(new_init, capsys):
    """AC14: 確認を通らない者は外して続ける。状態ファイルは作られ、理由が残る。"""
    st = new_init(failing={"kiro": "コマンドが見つかりません"})
    p = st["participants"]
    assert p["available"] == ["claude", "codex"]
    assert p["unavailable"] == {"kiro": "コマンドが見つかりません"}
    assert p["pool"] == ["claude", "codex", "kiro"]
    assert p["fallback"] == []
    assert p["probe_skipped"] is False
    assert p["require_all"] is False
    assert st["resume_changes"] == []
    assert st["max_rounds"] == 12 and st["rotate_after"] == 8
    err = capsys.readouterr().err
    assert err.count("⚠ kiro を担当から外しました（コマンドが見つかりません）") == 1


def test_require_all_keeps_the_old_gate(new_init, capsys):
    """AC15: `--require-all` では 1 者でも欠ければ終了コード 1 で、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        new_init("--require-all", failing={"kiro": "コマンドが見つかりません"})
    assert e.value.code == 1
    assert not new_init.state_file.exists()
    assert "kiro" in capsys.readouterr().err


def test_exclude_skips_the_probe_and_is_recorded(new_init):
    """AC16: `--exclude kiro` は kiro を確かめず、`excluded` に残す。"""
    st = new_init("--exclude", "kiro")
    assert new_init.calls == [["claude", "codex"]]
    assert st["participants"]["excluded"] == ["kiro"]
    assert st["participants"]["ignored_exclude"] == []
    assert st["participants"]["available"] == ["claude", "codex"]


def test_exclude_outside_the_default_pool_is_ignored_with_a_note(new_init, capsys):
    """#786 の AC4: 既定の母集合に無い agy の除外は止めずに無視し、1 行で知らせる。"""
    st = new_init("--exclude", "agy")
    assert new_init.calls == [["claude", "codex", "kiro"]]
    assert st["participants"]["excluded"] == []
    assert st["participants"]["ignored_exclude"] == ["agy"]
    assert st["participants"]["available"] == ["claude", "codex", "kiro"]
    err = capsys.readouterr().err
    assert err.count("ℹ --exclude agy は既定の母集合に無いため無視しました") == 1


def test_include_agy_puts_it_back_in_the_rotation(new_init, state_mod):
    """#786 の AC4: `--include agy` で agy が戻り、座席は 4 者の輪番になる。"""
    st = new_init("--include", "agy")
    assert st["participants"]["available"] == ["claude", "codex", "agy", "kiro"]
    seats = [state_mod._round_reviewers(st, r) for r in (1, 2, 3, 4)]
    assert seats == [["codex", "agy"], ["agy", "kiro"], ["claude", "kiro"], ["claude", "codex"]]


def test_only_agy_runs_alone_without_include(new_init, state_mod, tmp_path):
    """#786 の AC4b: `--only agy` は `--include agy` 無しでも agy 1 者で回る。"""
    st = new_init(only="agy")
    assert new_init.calls == [["agy"]]
    assert st["participants"]["available"] == ["agy"]
    assert st["participants"]["included"] == []
    assert _start_round(state_mod, tmp_path) == ["agy"]


def test_repeated_and_comma_separated_exclude_are_the_same(new_init):
    """AC16 後半: `--exclude agy --exclude kiro` と `--exclude agy,kiro` は同じ状態を作る。"""
    a = new_init("--exclude", "agy", "--exclude", "kiro")["participants"]
    new_init.state_file.unlink()
    b = new_init("--exclude", "agy,kiro")["participants"]
    assert a == b
    assert a["excluded"] == ["kiro"]
    assert a["ignored_exclude"] == ["agy"]
    assert a["available"] == ["claude", "codex"]


def test_include_the_host_changes_nothing_and_start_round_still_returns_two_seats(new_init, state_mod, tmp_path):
    """AC17: `--include claude` はエラーにならず 3 者のまま（既に母集合に入っている）。席は 2 つ。"""
    st = new_init("--include", "claude")
    assert st["participants"]["available"] == ["claude", "codex", "kiro"]
    assert st["participants"]["included"] == ["claude"]
    assert len(_start_round(state_mod, tmp_path)) == 2


def test_one_available_reviewer_is_backed_by_a_second_copy(new_init, state_mod, tmp_path, capsys):
    """#892 の AC5: 使える者が 1 者ならホストを別に確かめず、`<その者>-2` で埋める。"""
    st = new_init(failing={"claude": "未認証", "kiro": "未認証"})
    assert st["participants"]["available"] == ["codex"]
    assert st["participants"]["fallback"] == []
    assert new_init.calls == [["claude", "codex", "kiro"]]
    assert "⚠ 使える者が 1 者のため、席を同じランタイムの 2 つ目で埋めます（観点が減ります）" \
        in capsys.readouterr().err
    assert _start_round(state_mod, tmp_path) == ["codex", "codex-2"]


def test_only_does_not_probe_the_host_and_keeps_one_seat(new_init, state_mod, tmp_path):
    """AC18 後半: `--only codex` はホストを確かめず、席は 1 つ。"""
    st = new_init(only="codex")
    assert new_init.calls == [["codex"]]
    assert st["only"] == "codex"
    assert st["participants"]["fallback"] == []
    assert _start_round(state_mod, tmp_path) == ["codex"]


def test_only_fails_when_the_named_reviewer_does_not_pass_the_probe(new_init, capsys):
    """1 者指定でも使える者が 0 者なら止める（終了コード 1、状態ファイルを作らない）。

    1 者指定は席の埋め合わせをしないため、確認を通らない 1 者がそのまま席に座る。
    起動しても結果が残らず、**レビューが行われていないのに収束する**。
    """
    with pytest.raises(SystemExit) as e:
        new_init(only="codex", failing={"codex": "未認証"})
    assert e.value.code == 1
    assert not new_init.state_file.exists()
    assert new_init.calls == [["codex"]]
    err = capsys.readouterr().err
    assert "1 者指定の codex が確認を通りません" in err
    assert "未認証" in err


def test_only_still_starts_when_the_probe_is_skipped(new_init, state_mod, tmp_path, monkeypatch):
    """確認を飛ばした実行では、1 者指定はそのまま通る（通らなかった者がいない）。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(state_mod.auth, "probe_auth", _fake_probe({}, calls, skipped=True))
    p = state_mod._resolve_reviewers("claude", _init_args(tmp_path, only="codex"))
    assert p["available"] == ["codex"]
    assert p["probe_skipped"] is True


def test_no_available_reviewer_fails(new_init, capsys):
    """#892 の AC5: 使える者が 0 者なら、ホストを別に確かめず終了コード 1 で、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        new_init(failing={"codex": "x", "agy": "x", "kiro": "x", "claude": "x"})
    assert e.value.code == 1
    assert not new_init.state_file.exists()
    assert new_init.calls == [["claude", "codex", "kiro"]]
    err = capsys.readouterr().err
    assert "使える者がいません" in err
    assert "母集合 claude / codex / kiro の全員が確認を通りません" in err


def test_the_host_is_not_probed_separately_when_it_was_excluded(new_init, capsys):
    """#892 の AC5: ホストを外して 1 者になっても、ホストを埋め合わせに確かめない。"""
    st = new_init("--exclude", "claude", failing={"kiro": "x"})
    assert new_init.calls == [["codex", "kiro"]]
    assert st["participants"]["available"] == ["codex"]
    assert st["participants"]["fallback"] == []


# ---------- 読めないディレクトリを含む PATH（#813: AC10） ----------

def _stub_cli(bin_dir, *names: str) -> None:
    """確認コマンドが終了コード 0 で終わる短い実行ファイルを置く。"""
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)


def _real_subprocess(state_mod, monkeypatch):
    """認証の確認だけを本物の起動に戻す。

    `new_init` は子プロセスの起動を差し替える。差し替え先は標準ライブラリの同じ
    モジュールのため、属性を戻すと開始の手順の側まで本物になる。認証の確認が見る
    名前だけを別の入れ物へ向けて、2 つを分ける。
    """
    monkeypatch.setattr(state_mod.auth, "subprocess", types.SimpleNamespace(
        run=_REAL_RUN, TimeoutExpired=subprocess.TimeoutExpired))


def test_init_starts_when_an_unreadable_path_hides_a_missing_cli(
        new_init, state_mod, monkeypatch, tmp_path, capsys):
    """AC10: 読めないディレクトリを含む PATH で CLI が 1 つ欠けても、開始の手順は終わる。

    権限が効かない実行者（root）では、欠けた CLI の理由が「コマンドが見つかりません」に
    なる。外れて続くことは同じであり、理由の文言は認証の確認のテストが持つ。
    """
    _stub_cli(tmp_path / "bin", "codex", "agy")
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0o000)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{unreadable}")
    _real_subprocess(state_mod, monkeypatch)
    try:
        st = new_init("--include", "agy", real_probe=True)
    finally:
        unreadable.chmod(0o700)

    assert st["participants"]["available"] == ["codex", "agy"]
    assert list(st["participants"]["unavailable"]) == ["claude", "kiro"]
    assert _start_round(state_mod, tmp_path) == ["codex", "agy"]


def test_init_starts_when_a_probe_cannot_be_launched(new_init, state_mod, monkeypatch, tmp_path):
    """AC10: 起動できない例外がどの実行者でも「外して続ける」になることを確かめる。"""
    def run(cmd, **kwargs):
        if cmd[0] == "kiro-cli":
            raise PermissionError(13, "Permission denied")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(state_mod.auth, "subprocess", types.SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired))

    st = new_init(real_probe=True)

    assert st["participants"]["available"] == ["claude", "codex"]
    assert st["participants"]["unavailable"] == {
        "kiro": "コマンドを実行できません（Permission denied）"}


@pytest.mark.parametrize("argv", [
    ("--only", "codex", "--exclude", "codex"),
    ("--include", "agy", "--exclude", "agy"),
])
def test_contradicting_names_fail_before_the_state_is_written(new_init, argv):
    """AC20: 名前の矛盾は終了コード 1 で、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        new_init(*argv)
    assert e.value.code == 1
    assert not new_init.state_file.exists()
    assert new_init.calls == []


def test_none_mixed_with_a_name_is_rejected(new_init):
    with pytest.raises(SystemExit) as e:
        new_init("--exclude", "none,agy")
    assert e.value.code == 1
    assert not new_init.state_file.exists()


def test_none_in_the_new_path_means_unspecified(new_init):
    """決定 15: 新規の経路で `none` を渡すと、渡さないのと同じになる。"""
    st = new_init("--only", "none", "--exclude", "none", "--include", "none")
    assert st["only"] is None
    assert st["participants"]["excluded"] == []
    assert st["participants"]["included"] == []


def test_a_misspelt_runtime_is_rejected_by_argparse(state_mod):
    """名前の綴りは argparse の型が弾く（終了コード 2）。"""
    for words in (["--only", "gemini"], ["--exclude", "gemini"], ["--include", "codex,gemini"]):
        with pytest.raises(SystemExit) as e:
            state_mod.build_parser().parse_args(["init", "1", *words])
        assert e.value.code == 2


# ---------- 担当の読み出し（#727: AC22） ----------

def test_a_state_without_participants_keeps_the_old_rotation(state_mod, tmp_path):
    """AC22: `participants` が無くても、`host` があれば変更前の輪番と同じ値を返す。"""
    path = _state(tmp_path, host="codex")
    st = json.loads(path.read_text(encoding="utf-8"))
    # 変更前の輪番: 母集合 claude / agy / kiro から `(round_no - 1) % 3` の者を外した 2 者
    previous = [["agy", "kiro"], ["claude", "kiro"], ["claude", "agy"]]
    for round_no in range(1, 7):
        assert state_mod._round_reviewers(st, round_no) == previous[(round_no - 1) % 3]
    del st["host"]
    assert state_mod._round_reviewers(st, 1) == ["codex", "agy"]


def test_recorded_reviewers_win_over_only(state_mod, tmp_path):
    """決定 11: 再開で 1 者指定を変えても、記録のあるラウンドの担当は変わらない。"""
    path = _state(tmp_path, only="codex", rounds=[_round(1, ["agy", "kiro"], {})])
    st = json.loads(path.read_text(encoding="utf-8"))
    assert state_mod._round_reviewers(st, 1) == ["agy", "kiro"]
    assert state_mod._round_reviewers(st, 2) == ["codex"]


def test_participants_win_over_the_host_rotation(state_mod, tmp_path):
    """記録された参加者があれば、席の埋め方はその一覧から決める。"""
    path = _state(tmp_path, participants={
        "pool": ["codex", "agy", "kiro"], "included": [], "excluded": ["agy"],
        "available": ["codex", "kiro"], "unavailable": {}, "probe_skipped": False,
        "require_all": False, "fallback": [],
    })
    st = json.loads(path.read_text(encoding="utf-8"))
    assert state_mod._round_reviewers(st, 1) == ["codex", "kiro"]


# ---------- 母集合にホストを入れる（#892） ----------

def test_exclude_agy_on_claude_rotates_three_pairs(new_init, state_mod, tmp_path):
    """#892 の AC2: ホスト claude・`--exclude agy` で使える者は 3 者、ラウンド 1〜3 は 3 通りの組を 1 度ずつ。"""
    st = new_init("--exclude", "agy")
    available = st["participants"]["available"]
    assert available == ["claude", "codex", "kiro"]
    seats = [state_mod._round_reviewers(st, r) for r in (1, 2, 3)]
    assert seats == [["codex", "kiro"], ["claude", "kiro"], ["claude", "codex"]]
    assert {frozenset(s) for s in seats} == {
        frozenset(p) for p in (("claude", "codex"), ("claude", "kiro"), ("codex", "kiro"))}


@pytest.mark.parametrize("host", ["claude", "codex", "agy", "kiro"])
def test_init_reports_the_host_in_the_pool(state_mod, tmp_path, monkeypatch, capsys, host):
    """#892 の AC3: どのホストでも、`init` の出力の「母集合」にホストが入る。"""
    monkeypatch.setattr(state_mod.auth, "probe_auth", _fake_probe({}, []))
    args = _init_args(tmp_path)
    args.host = host
    p = state_mod._resolve_reviewers(host, args)
    assert host in p["pool"]
    expected = [r for r in state_mod.assignment.ALL_RUNTIMES if r in {"claude", "codex", "kiro", host}]
    assert f"母集合: {' / '.join(expected)} " in capsys.readouterr().err


def test_exclude_the_host_is_accepted(new_init):
    """#892 の AC4: `--exclude <ホスト>` を受け付け、ホストを外した母集合で始まる。"""
    st = new_init("--exclude", "claude")
    assert new_init.state_file.exists()
    assert "claude" not in st["participants"]["available"]
    assert st["participants"]["available"] == ["codex", "kiro"]
    assert st["participants"]["excluded"] == ["claude"]


def test_a_state_with_the_old_participants_keeps_its_seats(state_mod, tmp_path):
    """#892 の AC6: 変更の前に作った `participants`（使える者 2 者・`fallback: [host]`）は同じ席を返す。"""
    old = {
        "pool": ["codex", "agy", "kiro"], "included": [], "excluded": ["agy"],
        "available": ["codex", "kiro"], "unavailable": {}, "probe_skipped": False,
        "require_all": False, "fallback": ["claude"],
    }
    path = _state(tmp_path, participants=old)
    st = json.loads(path.read_text(encoding="utf-8"))
    for round_no in range(1, 13):
        assert state_mod._round_reviewers(st, round_no) == ["codex", "kiro"]
    one = dict(old, available=["codex"], unavailable={"kiro": "x"})
    path = _state(tmp_path, participants=one)
    st = json.loads(path.read_text(encoding="utf-8"))
    for round_no in range(1, 13):
        assert state_mod._round_reviewers(st, round_no) == ["codex", "claude"]


@pytest.mark.parametrize("host", ["claude", "codex", "agy", "kiro"])
def test_a_host_only_state_keeps_the_previous_rotation(state_mod, tmp_path, host):
    """#892 の AC7: `host` だけの状態は、全ランタイム − ホストの 3 者の輪番（ラウンド 1〜12）を保つ。"""
    path = _state(tmp_path, host=host)
    st = json.loads(path.read_text(encoding="utf-8"))
    previous_pool = [r for r in state_mod.assignment.ALL_RUNTIMES if r != host]
    for round_no in range(1, 13):
        dropped = (round_no - 1) % 3
        expected = [r for i, r in enumerate(previous_pool) if i != dropped]
        assert state_mod._round_reviewers(st, round_no) == expected, f"round={round_no}"


# ---------- 完了報告の「参加した者」（#727: AC24） ----------

def _report(state_mod, tmp_path, capsys, **over) -> list[str]:
    _state(tmp_path, final="approved", **over)
    state_mod.cmd_report(type("A", (), {"pr": 500})())
    out = capsys.readouterr().out
    body = out.split("## 参加した者\n", 1)
    assert len(body) == 2, out
    lines = []
    for line in body[1].splitlines():
        if line.startswith("## "):
            break
        if line.strip():
            lines.append(line)
    return lines


def test_the_report_lists_who_took_part(new_init, state_mod, tmp_path, capsys):
    """AC24 と #786 の AC4d: `init --exclude agy` で作った状態の完了報告に「参加した者」の節が出る。"""
    st = new_init("--exclude", "agy")
    capsys.readouterr()
    lines = _report(state_mod, tmp_path, capsys, participants=st["participants"])
    assert lines == [
        "- 母集合: claude / codex / kiro",
        "- 使える者: claude / codex / kiro",
        "- --exclude で外した者: なし",
        "- --exclude で指定したが既定の母集合に無かった者: agy",
        "- --include で足した者: なし",
        "- 確認を通らなかった者: なし",
        "- 席の埋め合わせ: なし",
        "- 再開で変えた値: なし",
    ]


def test_the_report_shows_the_reason_a_reviewer_was_dropped(state_mod, tmp_path, capsys):
    lines = _report(state_mod, tmp_path, capsys, participants={
        "pool": ["codex", "agy", "kiro"], "included": ["claude"], "excluded": [],
        "available": ["claude", "codex"], "unavailable": {"kiro": "コマンドが見つかりません"},
        "probe_skipped": False, "require_all": False, "fallback": ["claude"],
    })
    assert "- --include で足した者: claude" in lines
    assert "- 確認を通らなかった者: kiro（コマンドが見つかりません）" in lines
    assert "- 席の埋め合わせ: claude" in lines


def test_the_report_says_the_probe_was_skipped(state_mod, tmp_path, capsys):
    """確認を飛ばしたときは、通らなかった者が「なし」である理由を書き分ける。"""
    lines = _report(state_mod, tmp_path, capsys, participants={
        "pool": ["codex", "agy", "kiro"], "included": [], "excluded": [],
        "available": ["codex", "agy", "kiro"], "unavailable": {},
        "probe_skipped": True, "require_all": False, "fallback": [],
    })
    assert "- 確認を通らなかった者: 確認を飛ばした（NDF_SKIP_AUTH_CHECK）" in lines


def test_the_report_lists_the_resume_changes(state_mod, tmp_path, capsys):
    """再開で変えた値は 1 件 1 行で出す。"""
    lines = _report(state_mod, tmp_path, capsys, participants={
        "pool": ["codex", "agy", "kiro"], "included": [], "excluded": [],
        "available": ["codex", "agy", "kiro"], "unavailable": {},
        "probe_skipped": False, "require_all": False, "fallback": [],
    }, resume_changes=[
        {"at": "2026-09-19T12:00:00", "field": "max_rounds", "from": 12, "to": 20},
        {"at": "2026-09-19T12:00:00", "field": "only", "from": None, "to": "codex"},
    ])
    assert "- 再開で変えた値:" in lines
    assert "  - 2026-09-19T12:00:00 max_rounds: 12 → 20" in lines
    assert "  - 2026-09-19T12:00:00 only: None → codex" in lines


def test_a_state_without_participants_says_so(state_mod, tmp_path, capsys):
    """AC24 後半: `participants` を持たない状態ファイルでは「記録なし」と出す。"""
    assert _report(state_mod, tmp_path, capsys) == ["- 使える者: 記録なし"]
