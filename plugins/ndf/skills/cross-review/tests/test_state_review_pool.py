"""レビュワーの母集合と終了基準（#371）。

**母集合は「全ランタイム − ホスト」である。** 名指しの固定は、ホストが `codex` か `agy` の
ときに自分自身をレビュワーへ含める。担当はラウンドごとの輪番で 2 者を選ぶ。

**終了基準は「新しい指摘が出ない」である。** 全員 `APPROVE` は、最も止まらない参加者に
律速される。同じ論点の再提出では止まる。
"""
from __future__ import annotations

import json

import pytest


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

def test_reviewers_exclude_the_host(state_mod, tmp_path):
    """ラウンドの担当はホストを含まない 2 者である。"""
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
    """`read-result` は母集合の 4 者すべてを受け取る。

    実機で `kiro` が結果を書いたのに `invalid choice` で弾かれた。担当が 4 つの名前を
    取りうる以上、副コマンドの引数も同じ母集合を持たなければ、結果を残した担当が
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


def test_init_rejects_an_only_outside_the_pool(state_mod, tmp_path, monkeypatch):
    """母集合の外を `--only` に指定したら、起動する前に弾く。

    ホスト自身や、参加しないランタイムを指定しても、そのラウンドは 1 者も起動しない。
    """
    with pytest.raises(SystemExit):
        state_mod._validate_only("claude", "claude")   # ホスト自身
    assert state_mod._validate_only("codex", "claude") == "codex"
    assert state_mod._validate_only(None, "claude") is None


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


def test_auth_check_covers_only_the_reviewers_that_run(state_mod, monkeypatch):
    """`--only` を指定したときは、実際に起動する 1 者だけを確かめる。

    母集合の全員を確かめると、そのラウンドで起動しない CLI の未認証で `init` が
    失敗する。デバッグのために 1 者へ絞った意味が無くなる。
    """
    checked: list[list[str]] = []
    monkeypatch.setattr(state_mod.auth, "check_auth",
                        lambda rs, **k: checked.append(list(rs)) or {})
    assert state_mod._auth_targets("kiro", "claude") == ["kiro"]
    assert state_mod._auth_targets(None, "claude") == ["codex", "agy", "kiro"]


def test_the_host_hints_in_the_test_setup_match_the_shared_layer(state_mod):
    """テストが環境を整えるために写した手掛かりが、共通層とずれていないこと。

    ずれると、テストだけが古い手掛かりでホストを固定し続ける。
    """
    from conftest import _HOST_ENV_HINTS

    assert tuple(_HOST_ENV_HINTS) == tuple(state_mod.assignment.HOST_ENV_HINTS)


def test_init_fails_when_the_host_cannot_be_guessed(state_mod, monkeypatch):
    """手掛かりが無ければ、既定を置かずに失敗する。

    誤ると母集合が狂い、ホストが自分自身をレビューする。間違ったまま一周してしまい、
    成果物を見るまで気付けない。
    """
    for key, _ in state_mod.assignment.HOST_ENV_HINTS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(state_mod.assignment.AssignmentError):
        state_mod.assignment.detect_host(None, env={})
