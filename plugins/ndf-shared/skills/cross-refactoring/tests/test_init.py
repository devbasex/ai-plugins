"""`init` のテスト。

`gh` は呼ばないので `_sh` を差し替える。git は実際に動かし、
**書き込み用の作業ディレクトリが本当に作れるか**を確かめる。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")

HEAD_BRANCH = "refactor/target"


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def origin_repo(tmp_path):
    """origin にだけ head ブランチがあるリポジトリ。

    `git worktree add <path> <branch>` はローカルにブランチが無いと失敗するため、
    その経路を踏ませる形で用意する。
    """
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def f():\n    pass\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("checkout", "-qb", HEAD_BRANCH, cwd=repo)
    (repo / "src" / "bar.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "wip", cwd=repo)
    _git("push", "-q", "origin", HEAD_BRANCH, cwd=repo)
    # ローカルの head ブランチを消し、origin にだけある状態にする
    _git("checkout", "-q", "main", cwd=repo)
    _git("branch", "-qD", HEAD_BRANCH, cwd=repo)
    return repo


def _args(tmp_path, **over):
    base = {
        "pr": 130, "scope": ["src"], "host": "claude",
        "max_outer_rounds": 3, "max_fix_rounds": 3, "max_items_per_round": 5,
        "severity_threshold": "minor", "model": None, "baseline_test": "true",
        "sync_command": None,
        "test_timeout": 60,
        "worktree_root": str(tmp_path / "rf130"),
    }
    base.update(over)
    return type("A", (), base)()


@pytest.fixture
def run_init(refactor, origin_repo, monkeypatch):
    """`gh` 呼び出しだけを差し替えて `init` を走らせる。

    `viewer` は `gh api user` が返すログイン名。Pull Request の作成者は
    常に `author` なので、両者を一致させると自分の Pull Request になる。
    """
    def _run(args, viewer="someone-else"):
        real_sh = refactor._sh

        def fake_sh(cmd, cwd=None, check=True):
            if cmd[0] == "gh":
                if "nameWithOwner" in cmd:
                    return "acme/demo"
                if "headRefName" in cmd:
                    return HEAD_BRANCH
                if "baseRefName" in cmd:
                    return "main"
                if cmd[:3] == ["gh", "api", "user"]:
                    # viewer=None は取得に失敗する環境（bot トークンなど）を表す
                    if viewer is None:
                        if check:
                            refactor.die("コマンドが失敗しました (gh api user): HTTP 403")
                        return ""
                    return viewer
                if "author" in cmd:
                    return "me"
                raise AssertionError(f"想定外の gh 呼び出し: {cmd}")
            return real_sh(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(refactor, "_sh", fake_sh)
        monkeypatch.chdir(origin_repo)
        monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
        # 認証確認は実際の CLI を起動する。ここでは対象外なので飛ばす
        # （確認そのものは `test_init_checks_cli_authentication` で見る）。
        monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
        refactor.cmd_init(args)
    return _run


def _state_of(tmp_path):
    path = (tmp_path / "rf130" / "work" / ".cross_refactoring"
            / "cross-refactoring-rf130-state.json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_init_creates_the_writable_worktree_from_origin(run_init, tmp_path):
    """ローカルに head ブランチが無くても作業ディレクトリを作れること。"""
    run_init(_args(tmp_path))
    work = tmp_path / "rf130" / "work"
    assert (work / "src" / "bar.py").is_file()
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=work, capture_output=True, text=True)
    assert head.stdout.strip() == HEAD_BRANCH


def test_init_records_cohorts_separately(run_init, tmp_path):
    """提案・レビューと適用の母集合は別物である。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["codex", "gemini", "kiro"]
    assert state["impl_capable"] == ["claude", "codex", "kiro"]
    assert state["host"] == "claude"
    assert state["host_detection"] == "explicit"
    assert state["host"] not in state["runtimes"]


def test_init_records_models(run_init, tmp_path):
    run_init(_args(tmp_path, model=["codex=gpt-5.5", "kiro=claude-opus-5"]))
    _, state = _state_of(tmp_path)
    assert state["models"] == {
        "claude": None, "codex": "gpt-5.5", "gemini": None, "kiro": "claude-opus-5",
    }


def test_init_rejects_unknown_model_runtime(run_init, tmp_path):
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, model=["gpt=gpt-5.5"]))


def test_init_rejects_gemini_as_host(run_init, tmp_path):
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, host="gemini"))


def test_init_runs_the_baseline_test(run_init, tmp_path):
    run_init(_args(tmp_path, baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["baseline_test"]["status"] == "green"
    assert state["baseline_test"]["command"] == "true"


def test_init_refuses_to_start_when_the_baseline_test_fails(run_init, tmp_path):
    """壊れた状態から始めると、壊したのか元から壊れていたのか区別できない。"""
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, baseline_test="false"))


def test_baseline_test_is_required(refactor, monkeypatch):
    """振る舞い不変を示す手段が無い書き換えは構造改善ではないため、必須にする。"""
    monkeypatch.setattr(
        refactor.sys, "argv",
        ["refactor.py", "init", "130", "--scope", "src", "--host", "claude"],
    )
    with pytest.raises(SystemExit) as e:
        refactor.main()
    assert e.value.code == 2  # argparse の引数エラー


def test_init_is_idempotent(run_init, tmp_path, capsys):
    """再開時は既存の状態をそのまま返し、担当やモデルを作り直さない。"""
    run_init(_args(tmp_path, model=["codex=gpt-5.5"]))
    path, first = _state_of(tmp_path)
    first["rounds"].append({"round": 1, "impl": "codex"})
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

    run_init(_args(tmp_path, model=["codex=gpt-4"]))
    _, second = _state_of(tmp_path)
    assert second["models"]["codex"] == "gpt-5.5", "指定値が上書きされている"
    assert len(second["rounds"]) == 1, "状態が作り直されている"


def test_init_emits_shell_assignments(run_init, tmp_path, capsys):
    run_init(_args(tmp_path))
    out = capsys.readouterr().out
    assert "RUNTIMES_CSV=codex,gemini,kiro" in out
    # 空白を含む値は必ず引用する。引用しないと呼び出し側の eval で語が割れる。
    assert "RUNTIMES='codex gemini kiro'" in out
    assert "IMPL_POOL='claude codex kiro'" in out
    assert "TMP_DIR=" in out and "WORK=" in out


def test_existing_worktree_is_synced_to_origin(run_init, tmp_path, origin_repo):
    """再開までに head が進んでいたら、追いついてから始めること。

    同期せずに使うと、古い HEAD に対して提案・適用してしまう。
    """
    run_init(_args(tmp_path))
    work = tmp_path / "rf130" / "work"
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                            capture_output=True, text=True).stdout.strip()

    # origin 側だけを進める
    clone = tmp_path / "advance"
    subprocess.run(["git", "clone", "-q", "-b", HEAD_BRANCH,
                    str(tmp_path / "origin.git"), str(clone)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@e.st", cwd=clone)
    _git("config", "user.name", "test", cwd=clone)
    (clone / "src" / "baz.py").write_text("z = 1\n")
    _git("add", "-A", cwd=clone)
    _git("commit", "-qm", "advance", cwd=clone)
    _git("push", "-q", "origin", HEAD_BRANCH, cwd=clone)

    run_init(_args(tmp_path))

    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                           capture_output=True, text=True).stdout.strip()
    assert after != before, "origin の head へ同期していない"
    assert (work / "src" / "baz.py").is_file()


def test_diverged_worktree_stops_the_run(run_init, tmp_path):
    """早送りできない（履歴が分かれた）ときは中断すること。"""
    run_init(_args(tmp_path))
    work = tmp_path / "rf130" / "work"
    (work / "src" / "local.py").write_text("local = 1\n")
    _git("add", "-A", cwd=work)
    _git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-qm", "local only", cwd=work)

    clone = tmp_path / "advance2"
    subprocess.run(["git", "clone", "-q", "-b", HEAD_BRANCH,
                    str(tmp_path / "origin.git"), str(clone)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@e.st", cwd=clone)
    _git("config", "user.name", "test", cwd=clone)
    (clone / "src" / "remote.py").write_text("remote = 1\n")
    _git("add", "-A", cwd=clone)
    _git("commit", "-qm", "remote only", cwd=clone)
    _git("push", "-q", "origin", HEAD_BRANCH, cwd=clone)

    with pytest.raises(SystemExit):
        run_init(_args(tmp_path))


# ---------- 語彙と認証 ----------

def test_init_records_the_vocabulary_for_the_prompt(run_init, tmp_path, refactor):
    """許容値をプロンプトへ列挙できるよう、語彙集合を状態へ残すこと。

    手順書の見出しは日本語なので、「語彙に限定する」とだけ書くと読んだ側が
    日本語を語彙と解釈する（実測で gemini の提案 4 件が全件見送りになった）。
    """
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["vocabulary"]["smells"]["long_method"] == "長すぎるメソッド"
    assert "extract_method" in state["vocabulary"]["techniques"]
    assert state["vocabulary"]["severities"] == ["minor", "major", "critical"]
    # 定義は検証側の 1 箇所だけに置く
    assert state["vocabulary"]["smells"] == refactor.SMELLS


def _probe_result(refactor, monkeypatch, outcomes):
    """認証確認コマンドの結果を差し替える。`{ランタイム: (rc, 出力)}`。"""
    def fake_run(cmd, **kwargs):
        for runtime, probe in refactor.AUTH_PROBES.items():
            if list(cmd) == list(probe):
                rc, out = outcomes.get(runtime, (0, "ok"))
                return subprocess.CompletedProcess(cmd, rc, out, "")
        raise AssertionError(f"想定外の呼び出し: {cmd}")
    monkeypatch.setattr(refactor.subprocess, "run", fake_run)


def test_check_auth_passes_when_every_cli_is_logged_in(refactor, monkeypatch):
    monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
    _probe_result(refactor, monkeypatch, {})
    results = refactor.check_auth(["claude", "codex", "gemini", "kiro"])
    assert all(r["ok"] for r in results.values())


def test_check_auth_fails_on_a_non_zero_exit(refactor, monkeypatch):
    monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
    _probe_result(refactor, monkeypatch, {"kiro": (1, "")})
    with pytest.raises(SystemExit) as e:
        refactor.check_auth(["claude", "codex", "gemini", "kiro"])
    assert e.value.code == refactor.ABORT


def test_check_auth_fails_when_the_output_says_not_logged_in(refactor, monkeypatch):
    """終了コード 0 でも未認証を示すことがある（kiro は成否を終了コードで表さない）。"""
    monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
    _probe_result(refactor, monkeypatch, {"kiro": (0, "Not logged in")})
    with pytest.raises(SystemExit):
        refactor.check_auth(["claude", "codex", "gemini", "kiro"])


def test_check_auth_fails_when_the_cli_is_missing(refactor, monkeypatch):
    monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(refactor.subprocess, "run", missing)
    with pytest.raises(SystemExit):
        refactor.check_auth(["codex"])


def test_check_auth_can_be_skipped_explicitly(refactor, monkeypatch):
    """確認コマンドは CLI の版で変わる。飛ばせる逃げ道を残す。"""
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")

    def never(cmd, **kwargs):
        raise AssertionError("認証確認を実行してはいけない")

    monkeypatch.setattr(refactor.subprocess, "run", never)
    assert refactor.check_auth(["codex", "gemini"]) == {}


def test_init_checks_cli_authentication(refactor, origin_repo, monkeypatch, tmp_path):
    """未認証の CLI があれば初期化ごと中断すること。

    参加者が 1 人欠けた構成のまま進むと、その者の提案とレビューが無いまま収束する。
    """
    monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
    monkeypatch.chdir(origin_repo)
    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    _probe_result(refactor, monkeypatch, {"gemini": (1, "Authentication failed")})
    monkeypatch.setattr(
        refactor, "_sh",
        lambda cmd, **k: pytest.fail("認証確認より前に gh を呼んでいる"),
    )
    with pytest.raises(SystemExit) as e:
        refactor.cmd_init(_args(tmp_path))
    assert e.value.code == refactor.ABORT


def test_init_downgrades_the_posting_event_on_own_pull_request(run_init, tmp_path):
    """自分の Pull Request では投稿の event を `COMMENT` へ倒すこと。

    GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を
    `HTTP 422` で拒む。倒さないとレビュー担当が投稿に失敗する。
    """
    run_init(_args(tmp_path), viewer="me")
    _, state = _state_of(tmp_path)
    assert state["is_own_pr"] is True
    assert state["event_downgrade"] is True
    assert "COMMENT" in state["review_post_note"]


def test_init_keeps_the_posting_event_on_someone_elses_pull_request(run_init, tmp_path):
    """他者の Pull Request では判定をそのまま投稿すること。"""
    run_init(_args(tmp_path), viewer="someone-else")
    _, state = _state_of(tmp_path)
    assert state["is_own_pr"] is False
    assert state["event_downgrade"] is False
    assert "COMMENT" not in state["review_post_note"]


def test_init_continues_when_the_viewer_cannot_be_read(run_init, tmp_path):
    """ログイン名を読めない環境でも `init` を続けること。

    bot トークン（Actions の `GITHUB_TOKEN` など）は `/user` を読めず
    `HTTP 403` を返す。この値は自分の Pull Request かどうかの判定にしか
    使わないので、読めなければ他者の Pull Request として扱う。
    """
    run_init(_args(tmp_path), viewer=None)
    _, state = _state_of(tmp_path)
    assert state["is_own_pr"] is False
    assert state["event_downgrade"] is False


def test_init_fills_the_posting_event_when_resuming_an_old_state(run_init, tmp_path):
    """この指示が入る前の状態ファイルから再開しても投稿の event を倒すこと。

    再開の分岐は状態ファイルをそのまま使って戻る。項目が無い状態ファイルを
    そのまま渡すと、起動側は空の指示を読み、自分の Pull Request で
    `HTTP 422` を踏み続ける。
    """
    run_init(_args(tmp_path), viewer="me")
    path, state = _state_of(tmp_path)
    # 旧版が書いた状態ファイル（3 項目が無い）を再現する
    for key in ("is_own_pr", "event_downgrade", "review_post_note"):
        state.pop(key)
    state["outer_round"] = 2
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    run_init(_args(tmp_path), viewer="me")

    _, resumed = _state_of(tmp_path)
    assert resumed["outer_round"] == 2, "再開であって初期化ではないこと"
    assert resumed["is_own_pr"] is True
    assert resumed["event_downgrade"] is True
    assert "COMMENT" in resumed["review_post_note"]
