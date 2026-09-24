"""`init` のテスト。

`gh` は呼ばないので `sh` を差し替える。git は実際に動かし、
**書き込み用の作業ディレクトリが本当に作れるか**を確かめる。
"""
from __future__ import annotations

import sys
import json
import os
import pathlib
import shlex
import subprocess
import types

import pytest

from crossref_helpers import run_git


HEAD_BRANCH = "refactor/target"


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
    run_git("config", "user.email", "t@e.st", cwd=repo)
    run_git("config", "user.name", "test", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def f():\n    pass\n")
    run_git("add", "-A", cwd=repo)
    run_git("commit", "-qm", "init", cwd=repo)
    run_git("branch", "-M", "main", cwd=repo)
    run_git("push", "-q", "origin", "main", cwd=repo)
    run_git("checkout", "-qb", HEAD_BRANCH, cwd=repo)
    (repo / "src" / "bar.py").write_text("x = 1\n")
    run_git("add", "-A", cwd=repo)
    run_git("commit", "-qm", "wip", cwd=repo)
    run_git("push", "-q", "origin", HEAD_BRANCH, cwd=repo)
    # ローカルの head ブランチを消し、origin にだけある状態にする
    run_git("checkout", "-q", "main", cwd=repo)
    run_git("branch", "-qD", HEAD_BRANCH, cwd=repo)
    return repo


def _args(tmp_path, **over):
    base = {
        # **テストの置き場所を含める**（#436 決定 5）。含めないと `init` の関門で
        # 止まる。関門そのものは `test_scope_gate.py` で見る。
        "pr": 130, "scope": ["src", "tests"], "host": "claude",
        "ci_check": None, "workflow_step": False,
        "severity_threshold": "minor", "model": None, "baseline_test": "true",
        # **範囲のテストを既定で渡す**（#933 の AC3b）。`true` は既知の実行器でないため、
        # `--round-test` が無いと提案の前に止まる。全体のテストと同じ文字列なので、
        # 実行は 1 回で済む。関門そのものは下の AC3b のテストで見る。
        "round_test": "true",
        "sync_command": None, "plan_file": None,
        "worktree_root": str(tmp_path / "rf130"),
    }
    base.update(over)
    return type("A", (), base)()


@pytest.fixture
def run_init(refactor_lib, paths, patch_lib, refactor, origin_repo, monkeypatch):
    """`gh` 呼び出しだけを差し替えて `init` を走らせる。

    `viewer` は `gh api user` が返すログイン名。Pull Request の作成者は
    常に `author` なので、両者を一致させると自分の Pull Request になる。
    """
    refactor_lib = sys.modules["refactor_lib"]
    probed: list[list[str]] = []

    def _run(args, viewer="someone-else", probe=None, real_probe=False):
        """`probe` を渡すと確認を差し替える。`{ランタイム: 理由}` の者だけが通らない。

        `real_probe` を立てると差し替えず、止めない確認をそのまま走らせる（#813）。
        """
        real_sh = paths.sh

        def fake_sh(cmd, cwd=None, check=True):
            if cmd[0] == "gh":
                if "nameWithOwner" in cmd:
                    return "acme/demo"
                if cmd[:3] == ["gh", "api", "user"]:
                    # viewer=None は取得に失敗する環境（bot トークンなど）を表す
                    if viewer is None:
                        if check:
                            refactor_lib.die("コマンドが失敗しました (gh api user): HTTP 403")
                        return ""
                    return viewer
                # 作成者・head・base は REST の 1 回でまとめて返る（#271）。
                if len(cmd) == 3 and cmd[:2] == ["gh", "api"] and cmd[2].startswith("repos/"):
                    if cmd[2] != f"repos/acme/demo/pulls/{args.pr}":
                        return ""
                    return json.dumps({
                        "number": args.pr,
                        "user": {"login": "me"},
                        "head": {"ref": HEAD_BRANCH, "repo": {"full_name": "acme/demo"}},
                        "base": {"ref": "main"},
                    })
                raise AssertionError(f"想定外の gh 呼び出し: {cmd}")
            return real_sh(cmd, cwd=cwd, check=check)

        patch_lib("sh", fake_sh)
        monkeypatch.chdir(origin_repo)
        monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
        # 認証確認は実際の CLI を起動する。既定では飛ばし、`probe` を渡したときだけ
        # 止めない確認（`probe_auth`）を差し替えて結果を決める。
        if real_probe:
            monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
        elif probe is None:
            monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
        else:
            monkeypatch.delenv("NDF_SKIP_AUTH_CHECK", raising=False)
            cmd_setup = sys.modules["refactor_lib.commands.setup"]
            probed.clear()

            def fake_probe(runtimes, *, info, env=None):
                names = list(runtimes)
                probed.append(names)
                return {n: {"command": n, "ok": n not in probe, "detail": probe.get(n, "")}
                        for n in names}, False

            monkeypatch.setattr(cmd_setup.auth, "probe_auth", fake_probe)
        refactor.cmd_init(args)
    _run.probed = probed
    return _run


def refactor_abort():
    """中断の終了コード。`refactor` フィクスチャを取らない関数からも使う。"""
    return 4


def _state_path(tmp_path):
    return (tmp_path / "rf130" / "work" / ".cross_refactoring"
            / "cross-refactoring-rf130-state.json")


def _state_of(tmp_path):
    path = _state_path(tmp_path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_init_creates_the_writable_worktree_from_origin(run_init, tmp_path):
    """ローカルに head ブランチが無くても作業ディレクトリを作れること。"""
    run_init(_args(tmp_path))
    work = tmp_path / "rf130" / "work"
    assert (work / "src" / "bar.py").is_file()
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=work, capture_output=True, text=True)
    assert head.stdout.strip() == HEAD_BRANCH


def test_init_uses_codex_kiro_and_the_host_as_the_participants(run_init, tmp_path, capsys):
    """AC31 — 既定の参加者は codex / kiro とホスト。agy は確かめず、母集合は 1 つだけ。"""
    run_init(_args(tmp_path), probe={})
    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["claude", "codex", "kiro"]
    assert run_init.probed == [["claude", "codex", "kiro"]], "agy を確かめている"
    assert "impl_capable" not in state
    assert "IMPL_POOL=" not in capsys.readouterr().out
    assert state["participants"]["available"] == ["claude", "codex", "kiro"]
    assert state["participants"]["pool"] == ["claude", "codex", "kiro"]
    assert state["resume_changes"] == []
    assert state["host"] == "claude"
    assert state["host_detection"] == "explicit"


@pytest.mark.parametrize("host, expected", [
    ("codex", ["codex", "kiro"]),
    ("agy", ["codex", "agy", "kiro"]),
    ("kiro", ["codex", "kiro"]),
])
def test_the_participants_follow_the_host(run_init, tmp_path, host, expected):
    """AC32 — ホストが既定の参加者の表にいれば 2 者、いなければ 3 者になる。"""
    run_init(_args(tmp_path, host=host), probe={})
    assert _state_of(tmp_path)[1]["runtimes"] == expected


@pytest.mark.parametrize("over, expected", [
    ({"include": [["agy"]]}, ["claude", "codex", "agy", "kiro"]),
    ({"exclude": [["kiro"]]}, ["claude", "codex"]),
    ({"exclude": [["claude"]]}, ["codex", "kiro"]),
])
def test_include_and_exclude_change_the_participants(run_init, tmp_path, over, expected):
    """AC33 — 足す者・外す者で名指しで変えられる。ホストも母集合にいるので外せる。"""
    run_init(_args(tmp_path, **over), probe={})
    _, state = _state_of(tmp_path)
    assert state["runtimes"] == expected
    assert state["participants"]["included"] == over.get("include", [[]])[0]
    assert state["participants"]["excluded"] == over.get("exclude", [[]])[0]


def test_a_failed_probe_drops_the_runtime_and_keeps_going(run_init, tmp_path, capsys):
    """AC35 — 1 者の確認が通らなくても止めず、理由を残して使える者で始める。"""
    run_init(_args(tmp_path), probe={"kiro": "Not logged in"})
    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["claude", "codex"]
    assert state["participants"]["unavailable"] == {"kiro": "Not logged in"}
    assert "kiro を担当から外しました（Not logged in）" in capsys.readouterr().err


def test_init_starts_when_an_unreadable_path_hides_a_missing_cli(
        run_init, tmp_path, monkeypatch, capsys):
    """AC11 — 読めないディレクトリを含む PATH で CLI が 1 つ欠けても始まる（#813）。

    確認コマンドは、終わりが分かる短い実行ファイルへ差し替える。欠ける 1 者だけは
    どこにも無い名前にし、読めないディレクトリを PATH の末尾へ足す。
    """
    cmd_setup = sys.modules["refactor_lib.commands.setup"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ndf-stub-ok"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0o000)
    monkeypatch.setattr(cmd_setup.auth, "AUTH_PROBES", {
        "claude": ("ndf-stub-ok",), "codex": ("ndf-stub-ok",),
        "agy": ("ndf-stub-ok",), "kiro": ("ndf-stub-missing",),
    })
    monkeypatch.setenv("PATH", f"{os.environ['PATH']}:{bin_dir}:{unreadable}")
    try:
        run_init(_args(tmp_path), real_probe=True)
    finally:
        unreadable.chmod(0o700)

    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["claude", "codex"]
    assert list(state["participants"]["unavailable"]) == ["kiro"]
    assert "kiro を担当から外しました" in capsys.readouterr().err


def test_init_starts_when_a_probe_cannot_be_launched(run_init, tmp_path, monkeypatch):
    """AC11 — 起動できない例外は、権限が効かない実行者でも「外して続ける」になる。"""
    cmd_setup = sys.modules["refactor_lib.commands.setup"]

    def run(cmd, **kwargs):
        if cmd[0] == "kiro-cli":
            raise PermissionError(13, "Permission denied")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # 差し替える先は認証の確認が見る名前だけにする。標準ライブラリの属性を差し替えると、
    # 同じモジュールを使う git の呼び出しまで偽物になる。
    monkeypatch.setattr(cmd_setup.auth, "subprocess", types.SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired))
    run_init(_args(tmp_path), real_probe=True)

    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["claude", "codex"]
    assert state["participants"]["unavailable"] == {
        "kiro": "コマンドを実行できません（Permission denied）"}


def test_require_all_stops_without_writing_the_state(run_init, tmp_path):
    """AC35 — 全員を要する指定では従来の関門で止め、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, require_all=True), probe={"kiro": "Not logged in"})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


def test_no_available_runtime_stops_without_writing_the_state(run_init, tmp_path, capsys):
    """AC36 — 使える者が 0 者なら終了コード 4 で止め、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path), probe={"claude": "x", "codex": "y", "kiro": "z"})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()
    assert "使える者がいません" in capsys.readouterr().err


def test_excluding_a_runtime_outside_the_pool_is_ignored(run_init, tmp_path, capsys):
    """#786 の AC4c — 母集合に無い agy の除外は中断せず、`ℹ` の 1 行を出して続ける。"""
    run_init(_args(tmp_path, exclude=[["agy"]]), probe={})
    _, state = _state_of(tmp_path)
    assert state["participants"]["excluded"] == []
    assert state["participants"]["ignored_exclude"] == ["agy"]
    assert state["runtimes"] == ["claude", "codex", "kiro"]
    assert "ℹ --exclude agy は既定の母集合に無いため無視しました" in capsys.readouterr().err


@pytest.mark.parametrize("over", [
    {"include": [["agy"]], "exclude": [["agy"]]},   # 足す者と外す者の重なり
])
def test_contradicting_names_stop_the_init(run_init, tmp_path, over):
    """名前の矛盾は共通層が弾き、この工程の中断（終了コード 4）へ写す。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, **over), probe={})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


@pytest.mark.parametrize("over", [
    {"exclude": [["none", "kiro"]]},
    {"include": [["none", "agy"]]},
])
def test_none_mixed_with_runtime_names_stops_the_init(run_init, tmp_path, over):
    """none とランタイム名の混在は中断（終了コード 4）し、状態ファイルを作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, **over), probe={})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


def test_init_records_models(run_init, tmp_path):
    run_init(_args(tmp_path, model=["codex=gpt-5.5", "kiro=claude-opus-5"]))
    _, state = _state_of(tmp_path)
    assert state["models"] == {
        "claude": None, "codex": "gpt-5.5", "agy": None, "kiro": "claude-opus-5",
    }


def test_init_warns_that_the_kiro_default_model_cannot_be_measured(run_init, tmp_path, capsys):
    """既定の `auto` は実際に動いたモデルを取得できず、集計から分離される。

    報告まで分からないと、比較のために回した実行が丸ごと無駄になる。
    止めはしない（比較が目的でない実行もある）。
    """
    run_init(_args(tmp_path))
    warning = capsys.readouterr().err
    assert "kiro のモデルが default です" in warning
    assert "--model kiro=<モデル名>" in warning
    _, state = _state_of(tmp_path)
    assert state["models"]["kiro"] is None, "警告だけで、指定は書き換えないこと"


def test_init_warns_when_kiro_is_given_auto_explicitly(run_init, tmp_path, capsys):
    run_init(_args(tmp_path, model=["kiro=auto"]))
    assert "kiro のモデルが auto です" in capsys.readouterr().err


def test_init_warns_when_codex_or_agy_has_no_model(run_init, tmp_path, capsys):
    """実測できないランタイムで指定が無いラウンドも、kiro の auto と同じく分離される。

    警告の対象は参加者だけである。既定で外れる agy は、足したときだけ警告する。
    """
    run_init(_args(tmp_path, model=["kiro=claude-opus-5"]))
    warning = capsys.readouterr().err
    assert "codex のモデルが default です" in warning
    assert "agy のモデルが" not in warning


def test_init_warns_about_agy_when_it_is_included(run_init, tmp_path, capsys):
    run_init(_args(tmp_path, model=["kiro=claude-opus-5"], include=[["agy"]]))
    assert "agy のモデルが default です" in capsys.readouterr().err


def test_init_does_not_warn_when_every_model_can_be_measured(run_init, tmp_path, capsys):
    """claude だけは指定が無くても実測できるため、警告の対象にならない。"""
    run_init(_args(tmp_path, model=[
        "codex=gpt-5.5", "agy=gemini-3.8", "kiro=claude-opus-5",
    ], include=[["agy"]]))
    assert "集計から分離されます" not in capsys.readouterr().err


def test_init_rejects_unknown_model_runtime(run_init, tmp_path):
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, model=["gpt=gpt-5.5"]))


def test_init_accepts_agy_as_host(run_init, tmp_path):
    """agy も NDF の配布先であるため、ホストになれる。"""
    run_init(_args(tmp_path, host="agy"))
    _, state = _state_of(tmp_path)
    assert state["host"] == "agy"
    assert state["runtimes"] == ["codex", "agy", "kiro"]


def test_init_runs_the_baseline_test(run_init, tmp_path):
    run_init(_args(tmp_path, baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["baseline_test"]["status"] == "green"
    assert state["baseline_test"]["command"] == "true"


def test_init_refuses_to_start_when_the_baseline_test_fails(run_init, tmp_path):
    """壊れた状態から始めると、壊したのか元から壊れていたのか区別できない。"""
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, baseline_test="false"))


def test_init_stops_when_the_scope_has_no_test_location(run_init, tmp_path):
    """C3 — テストの置き場所が範囲に無ければ**止める**（#436 決定 5）。

    案内だけでは同じ失敗を繰り返す。止めれば、利用者は 1 度だけ範囲を直せばよい。
    """
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, scope=["src"]))
    assert e.value.code == refactor_abort()


def test_init_stops_when_the_test_location_is_outside_the_baseline_search(run_init, tmp_path, origin_repo):
    """C3 — 足したテストが `--baseline-test` で実行されないなら止める。"""
    (origin_repo / "src" / "unit").mkdir(parents=True, exist_ok=True)
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, scope=["src", "tests"], round_test=None,
                       baseline_test="pytest src/unit"))
    assert e.value.code == refactor_abort()


def test_init_checks_the_scope_before_running_the_baseline_test(run_init, tmp_path, test_calls):
    """関門は着手前のテストより先に通す。落ちる実行に時間を使わせない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, scope=["src"], baseline_test="false"))
    assert e.value.code == refactor_abort()
    assert test_calls.seen == []


def _parsed_init_args(patch_lib, refactor, monkeypatch, *extra):
    """`init` の引数を解析だけして返す。"""
    captured = {}
    # **入口の名前を差し替える。** `main()` は `refactor.py` が取り込んだ `cmd_init` を
    # 呼ぶ。`patch_lib` が見るのは `refactor_lib` 配下だけなので、ここには届かない。
    monkeypatch.setattr(refactor, "cmd_init", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(
        refactor.sys, "argv",
        ["refactor.py", "init", "130", "--scope", "src", "--host", "claude",
         "--baseline-test", "true", *extra],
    )
    refactor.main()
    return captured


def test_the_caps_are_unset_in_the_arguments(patch_lib, refactor, monkeypatch):
    """引数の既定は未指定で、再開で「渡さなかった」と読める（#727 の決定 13）。

    廃止した 3 つの引数も未指定のまま受け取る。渡したかどうかで知らせを出すため。
    """
    captured = _parsed_init_args(patch_lib, refactor, monkeypatch)
    for key in ("budget_minutes", "implementer", "max_fix_rounds", "test_timeout",
                "severity_threshold", "workflow_step", "include", "exclude",
                "require_all", "max_test_rounds", "max_outer_rounds",
                "max_items_per_round"):
        assert captured[key] is None, key


def test_a_new_run_fills_the_caps_with_their_defaults(run_init, tmp_path):
    """AC1 決定 24 — 新規の初期化が現行の既定（予算 30 分）へ置き換え、上限の表を書き出す。

    ラウンド制の上限（提案・テスト整備・採用の件数）と、修正の回数・テスト 1 回の上限の
    固定値は状態に載せない（#933 の決定 5・決定 24）。
    """
    run_init(_args(tmp_path, severity_threshold=None, workflow_step=None))
    _, state = _state_of(tmp_path)
    assert state["budget_minutes"] == 30
    assert state["severity_threshold"] == "minor"
    assert state["workflow_step"] is False
    limits = state["limits"]
    assert limits["init_test_timeout"] == 180 and limits["margin_seconds"] == 90
    # 着手前の全体のテスト（`true`）はほぼ 0 秒なので、下限の 0.01·B が効く
    assert limits["test_timeout"] == 18
    for key in ("propose_end_at", "plan_end_at", "final_end_at"):
        assert limits[key], key
    for key in ("max_outer_rounds", "max_test_rounds", "max_items_per_round",
                "outer_round", "round_kind", "rounds", "max_fix_rounds", "test_timeout"):
        assert key not in state, key


# ---------- 想定最大時間 `--budget-minutes`（#933 の AC1） ----------

def test_the_budget_is_recorded(run_init, tmp_path, capsys):
    """整数の文字列を受け取り、整数として状態へ残す（引数は argparse で型を付けない）。"""
    run_init(_args(tmp_path, budget_minutes="90"))
    _, state = _state_of(tmp_path)
    assert state["budget_minutes"] == 90
    assert "BUDGET_MINUTES=90" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", ""])
def test_an_invalid_budget_stops_before_anything_runs(run_init, tmp_path, test_calls, value):
    """AC1 — 1 以上の整数でなければ終了コード 4 で止め、テストも状態も作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, budget_minutes=value))
    assert e.value.code == refactor_abort()
    assert test_calls.seen == []
    assert not _state_path(tmp_path).exists()


def test_an_invalid_budget_is_not_an_argparse_error(patch_lib, refactor, monkeypatch):
    """AC1 — 型の検査は `init` が行う。argparse の終了コード 2 にしない。"""
    captured = _parsed_init_args(patch_lib, refactor, monkeypatch, "--budget-minutes", "abc")
    assert captured["budget_minutes"] == "abc"


# ---------- 廃止した引数（#933 の AC2） ----------

@pytest.mark.parametrize("arg", ["max_test_rounds", "max_outer_rounds", "max_items_per_round",
                                 "max_fix_rounds", "test_timeout"])
def test_a_deprecated_argument_is_announced_and_ignored(run_init, tmp_path, capsys, arg):
    """AC2 — 渡すと「廃止」と引数名を標準エラーへ出し、止めずに初期化を終える。"""
    run_init(_args(tmp_path, **{arg: "2"}))
    err = capsys.readouterr().err
    option = "--" + arg.replace("_", "-")
    lines = [line for line in err.splitlines() if option in line and "廃止" in line]
    assert len(lines) == 1, err
    _, state = _state_of(tmp_path)
    assert arg not in state


def test_a_deprecated_argument_is_still_parsed(patch_lib, refactor, monkeypatch):
    """AC2 — argparse は廃止の引数を拒まない（呼び出し側の手順が壊れない）。"""
    captured = _parsed_init_args(patch_lib, refactor, monkeypatch,
                                 "--max-test-rounds", "2", "--max-outer-rounds", "3",
                                 "--max-items-per-round", "5")
    assert (captured["max_test_rounds"], captured["max_outer_rounds"],
            captured["max_items_per_round"]) == ("2", "3", "5")


def test_no_deprecation_notice_without_the_arguments(run_init, tmp_path, capsys):
    run_init(_args(tmp_path))
    assert "廃止" not in capsys.readouterr().err


# ---------- 項目ごとのテストを組み立てられるか（#933 の AC3b） ----------

def test_an_unknown_baseline_without_a_round_test_stops(run_init, tmp_path, test_calls):
    """AC3b — `--round-test` が無く `--baseline-test` が既知の実行器でなければ止める。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, round_test=None, baseline_test="make test"))
    assert e.value.code == refactor_abort()
    assert test_calls.seen == [], "テストに時間を使う前に止める"
    assert not _state_path(tmp_path).exists()


def test_a_known_baseline_without_a_round_test_starts(run_init, tmp_path, test_calls):
    """AC3b — `pytest -q` なら項目ごとのテストを組み立てられるので通る。"""
    run_init(_args(tmp_path, round_test=None, baseline_test="pytest -q"))
    _, state = _state_of(tmp_path)
    assert state["baseline_test"]["command"] == "pytest -q"
    assert state["round_test"]["command"] is None


def test_an_unknown_baseline_with_a_round_test_starts(run_init, tmp_path, test_calls):
    """AC3b — `--round-test` があれば、全体のテストが既知でなくても通る。"""
    run_init(_args(tmp_path, round_test="true", baseline_test="make test"))
    assert _state_path(tmp_path).exists()


def test_include_and_exclude_parse_names_and_none(patch_lib, refactor, monkeypatch):
    """カンマ区切りと繰り返しの両方を受ける。綴りの誤りは argparse が弾く。"""
    captured = _parsed_init_args(patch_lib, refactor, monkeypatch,
                                 "--exclude", "kiro", "--include", "agy,claude",
                                 "--require-all")
    assert captured["exclude"] == [["kiro"]]
    assert captured["include"] == [["agy", "claude"]]
    assert captured["require_all"] is True
    assert _parsed_init_args(patch_lib, refactor, monkeypatch,
                             "--exclude", "none")["exclude"] == [["none"]]
    with pytest.raises(SystemExit) as e:
        _parsed_init_args(patch_lib, refactor, monkeypatch, "--exclude", "gemini")
    assert e.value.code == 2


@pytest.mark.parametrize("empty", ["", "   ", ","])
def test_empty_include_is_rejected_before_init_runs(
        patch_lib, refactor, monkeypatch, empty):
    """R1-004 — 空の `--include` は argparse の型が弾き、初期化へ進まない。

    `runtime_list` が空の値で `ArgumentTypeError` を上げ、argparse が終了コード 2 で
    止める。`cmd_init` は差し替えた入口を通らないため、捕えた引数は空のままになる。
    """
    captured = {}
    monkeypatch.setattr(refactor, "cmd_init",
                        lambda args: captured.update(vars(args)))
    monkeypatch.setattr(
        refactor.sys, "argv",
        ["refactor.py", "init", "130", "--scope", "src", "--host", "claude",
         "--baseline-test", "true", "--include", empty],
    )
    with pytest.raises(SystemExit) as e:
        refactor.main()
    assert e.value.code == 2
    assert captured == {}, "初期化処理へ進んでいる"


def test_none_mixed_with_a_runtime_name_in_exclude_stops_the_init(run_init, tmp_path):
    """R1-004 — none と実行者名を混在させた `--exclude` は中断し、状態を作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, exclude=[["none", "kiro"]]), probe={})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


def test_the_ci_check_is_not_set_by_default(patch_lib, refactor, monkeypatch):
    """指定が無ければ代替しない。**手元のテストで判定する**（決定 7 の排他）。"""
    assert _parsed_init_args(patch_lib, refactor, monkeypatch)["ci_check"] is None
    assert _parsed_init_args(patch_lib, refactor, monkeypatch, "--ci-check", "tests")["ci_check"] == "tests"


def test_init_starts_at_the_propose_phase(run_init, tmp_path):
    """版 2 の状態は提案のフェーズから始まり、計画はまだ無い（#933）。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["schema"] == 2
    assert state["phase"] == "propose"
    assert state["plan"] is None
    assert state["phases"] == {}
    assert state["candidates"] == [] and state["items"] == []


def test_init_records_the_ci_check(run_init, tmp_path):
    run_init(_args(tmp_path, ci_check="tests"))
    _, state = _state_of(tmp_path)
    assert state["ci_check"] == "tests"


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
    first["candidates"].append({"id": "C-001"})
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

    run_init(_args(tmp_path, model=["codex=gpt-4"]))
    _, second = _state_of(tmp_path)
    assert second["models"]["codex"] == "gpt-5.5", "指定値が上書きされている"
    assert second["candidates"] == [{"id": "C-001"}], "状態が作り直されている"
    assert second["started_at"] == first["started_at"]


def test_init_emits_shell_assignments(run_init, tmp_path, capsys):
    run_init(_args(tmp_path))
    out = capsys.readouterr().out
    assert "RUNTIMES_CSV=claude,codex,kiro" in out
    # 空白を含む値は必ず引用する。引用しないと呼び出し側の eval で語が割れる。
    assert "RUNTIMES='claude codex kiro'" in out
    assert "IMPL_POOL=" not in out
    assert "TMP_DIR=" in out and "WORK=" in out
    # AC24 — 駆動は再開の地点と実装担当を出力から読む。
    assert "PHASE=propose" in out.splitlines()
    assert "IMPL=claude" in out.splitlines()


def test_init_no_longer_emits_a_fixed_stall_timeout(run_init, tmp_path, capsys):
    """決定 24: 無音の許容は段の上限と同じ値を `start-phase` が返す。`init` は出さない。"""
    run_init(_args(tmp_path))
    assert "IMPL_STALL_TIMEOUT=" not in capsys.readouterr().out


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
    run_git("config", "user.email", "t@e.st", cwd=clone)
    run_git("config", "user.name", "test", cwd=clone)
    (clone / "src" / "baz.py").write_text("z = 1\n")
    run_git("add", "-A", cwd=clone)
    run_git("commit", "-qm", "advance", cwd=clone)
    run_git("push", "-q", "origin", HEAD_BRANCH, cwd=clone)

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
    run_git("add", "-A", cwd=work)
    run_git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-qm", "local only", cwd=work)

    clone = tmp_path / "advance2"
    subprocess.run(["git", "clone", "-q", "-b", HEAD_BRANCH,
                    str(tmp_path / "origin.git"), str(clone)],
                   check=True, capture_output=True)
    run_git("config", "user.email", "t@e.st", cwd=clone)
    run_git("config", "user.name", "test", cwd=clone)
    (clone / "src" / "remote.py").write_text("remote = 1\n")
    run_git("add", "-A", cwd=clone)
    run_git("commit", "-qm", "remote only", cwd=clone)
    run_git("push", "-q", "origin", HEAD_BRANCH, cwd=clone)

    with pytest.raises(SystemExit):
        run_init(_args(tmp_path))


# ---------- 語彙と認証 ----------

def test_init_records_the_vocabulary_for_the_prompt(run_init, tmp_path, vocabulary):
    """許容値をプロンプトへ列挙できるよう、語彙集合を状態へ残すこと。

    手順書の見出しは日本語なので、「語彙に限定する」とだけ書くと読んだ側が
    日本語を語彙と解釈する（実測で agy の提案 4 件が全件見送りになった）。
    """
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["vocabulary"]["smells"]["long_method"] == "長すぎるメソッド"
    assert "extract_method" in state["vocabulary"]["techniques"]
    assert state["vocabulary"]["severities"] == ["minor", "major", "critical"]
    # 定義は検証側の 1 箇所だけに置く
    assert state["vocabulary"]["smells"] == vocabulary.SMELLS



# ---------- 再開（#727 / #648 の決定 13〜16） ----------

def test_resume_before_the_plan_rebuilds_the_limits_from_the_new_budget(run_init, tmp_path):
    """決定 24 — 計画の前に予算を置き換えた再開は、上限の表を新しい予算で組み直す。"""
    run_init(_args(tmp_path, budget_minutes="30"))
    run_init(_args(tmp_path, budget_minutes="10"))
    _, after = _state_of(tmp_path)
    assert after["budget_minutes"] == 10
    assert after["limits"]["init_test_timeout"] == 60 and after["limits"]["margin_seconds"] == 30


@pytest.mark.parametrize("over, option", [
    ({"model": ["codex=x"]}, "--model"),
    ({"host": "codex"}, "--host"),
    ({"scope": ["other", "tests"]}, "--scope"),
    ({"baseline_test": "pytest -q"}, "--baseline-test"),
    ({"severity_threshold": "major"}, "--severity-threshold"),
    ({"implementer": "codex"}, "--implementer"),
])
def test_resume_notifies_arguments_it_does_not_reflect(
        run_init, tmp_path, capsys, origin_repo, over, option):
    """AC39 — 反映しない引数は状態を変えず、引数ごとに 1 行知らせる。"""
    (origin_repo / "other").mkdir(exist_ok=True)
    run_init(_args(tmp_path))
    path, before = _state_of(tmp_path)
    capsys.readouterr()

    run_init(_args(tmp_path, **over))
    _, after = _state_of(tmp_path)
    err = capsys.readouterr().err
    assert f"ℹ {option} は再開では反映しません" in err
    for key in ("models", "host", "target_scope", "baseline_test", "severity_threshold",
                "implementer", "implementer_named"):
        assert after[key] == before[key], key
    assert after["resume_changes"] == []


def test_resume_without_arguments_changes_nothing(run_init, tmp_path, capsys, test_calls):
    """AC39 — 何も渡さない再開では、上限・モデル・参加者が変わらず、確認もしない。

    `--round-test` は省く。全体のテストと同じ文字列を渡すと状態には省いた形で残り、
    再開で同じ引数を渡しても「反映しない」と知らせる（本体の既知の振る舞い）。
    """
    run_init(_args(tmp_path, round_test=None, baseline_test="pytest -q"), probe={})
    _, before = _state_of(tmp_path)

    run_init(_args(tmp_path, round_test=None, baseline_test="pytest -q"),
             probe={"kiro": "Not logged in"})
    _, after = _state_of(tmp_path)
    assert run_init.probed == [], "担当に関わる引数を渡していないのに確かめ直している"
    for key in ("budget_minutes", "limits", "models",
                "runtimes", "participants", "implementer"):
        assert after[key] == before[key], key
    assert "再開では反映しません" not in capsys.readouterr().err


def test_resume_with_exclude_rebuilds_the_participants(run_init, tmp_path):
    """AC40 — 外す者を渡した再開では確かめ直し、渡さなかった足す者は記録から補う。"""
    run_init(_args(tmp_path, include=[["agy"]]), probe={})
    run_init(_args(tmp_path, exclude=[["kiro"]]), probe={})
    _, state = _state_of(tmp_path)
    assert run_init.probed == [["claude", "codex", "agy"]]
    assert state["runtimes"] == ["claude", "codex", "agy"]
    assert state["participants"]["included"] == ["agy"]
    assert state["participants"]["excluded"] == ["kiro"]
    changes = state["resume_changes"]
    assert [c["field"] for c in changes] == ["participants"], "作り直しは 1 件として積む"
    assert changes[0]["from"]["available"] == ["claude", "codex", "agy", "kiro"]


def test_resume_keeps_an_ignored_exclusion(run_init, tmp_path):
    """#786 の AC4d — `--exclude agy` で始めて `--include` だけで再開しても、無視した除外が残る。"""
    run_init(_args(tmp_path, exclude=[["agy"]]), probe={})
    run_init(_args(tmp_path, include=[["claude"]]), probe={})
    _, state = _state_of(tmp_path)
    assert state["participants"]["ignored_exclude"] == ["agy"]
    assert state["participants"]["excluded"] == []


def test_resume_keeps_real_and_ignored_exclusions_together(run_init, tmp_path):
    """現状固定: 再開時は実際の除外と母集合外の除外をともに足し戻す。"""
    run_init(_args(tmp_path, exclude=[["kiro", "agy"]]), probe={})
    _, before = _state_of(tmp_path)
    assert before["participants"]["excluded"] == ["kiro"]
    assert before["participants"]["ignored_exclude"] == ["agy"]

    run_init(_args(tmp_path, include=[["claude"]]), probe={})

    _, state = _state_of(tmp_path)
    assert state["participants"]["excluded"] == ["kiro"]
    assert state["participants"]["ignored_exclude"] == ["agy"]
    assert state["runtimes"] == ["claude", "codex"]


def test_resume_include_wins_over_an_ignored_exclusion(run_init, tmp_path):
    """無視した除外の名前を `--include` で渡すと、足し戻さずに参加者へ戻す。"""
    run_init(_args(tmp_path, exclude=[["agy"]]), probe={})
    run_init(_args(tmp_path, include=[["agy"]]), probe={})
    _, state = _state_of(tmp_path)
    assert state["participants"]["ignored_exclude"] == []
    assert state["runtimes"] == ["claude", "codex", "agy", "kiro"]


def test_resume_include_of_a_real_exclusion_still_conflicts(run_init, tmp_path):
    """実際に外した者を `--include` で渡すと、矛盾として中断し状態を書き換えない（R2-003 の現状固定）。"""
    run_init(_args(tmp_path, exclude=[["kiro"]]), probe={})
    path, state = _state_of(tmp_path)
    assert state["participants"]["excluded"] == ["kiro"]
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, include=[["kiro"]]), probe={})
    assert e.value.code == refactor_abort()
    assert path.read_text(encoding="utf-8") == before


def test_resume_with_include_adds_the_participant_worktree(run_init, tmp_path):
    """足す者を渡した再開では参加者と作業ツリーの対応をともに補う。"""
    run_init(_args(tmp_path), probe={})

    run_init(_args(tmp_path, include=[["agy"]]), probe={})

    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["claude", "codex", "agy", "kiro"]
    assert state["worktrees"]["agy"] == str(tmp_path / "rf130" / "agy")
    changes = state["resume_changes"]
    assert [change["field"] for change in changes] == ["participants"]
    assert changes[0]["to"]["available"] == state["runtimes"]


def test_resume_with_none_clears_the_recorded_names(run_init, tmp_path):
    """予約語 `none` は記録の一覧を空へ戻す（決定 15）。"""
    run_init(_args(tmp_path, exclude=[["kiro"]]), probe={})
    run_init(_args(tmp_path, exclude=[["none"]]), probe={})
    _, state = _state_of(tmp_path)
    assert state["participants"]["excluded"] == []
    assert state["runtimes"] == ["claude", "codex", "kiro"]


def test_a_failed_rebuild_leaves_the_state_untouched(run_init, tmp_path):
    """作り直しが 0 者なら終了コード 4 で止め、状態ファイルを書き換えない。"""
    run_init(_args(tmp_path), probe={})
    path, _ = _state_of(tmp_path)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, exclude=[["kiro"]], max_fix_rounds=9),
                 probe={"claude": "x", "codex": "y"})
    assert e.value.code == refactor_abort()
    assert path.read_text(encoding="utf-8") == before


# ---------- 範囲のテスト `--round-test`（#880 の AC1・AC4・AC5） ----------

@pytest.fixture
def test_calls(patch_lib):
    """`init` が実行したテストのコマンドを記録する。終了コードはコマンドごとに決める。"""
    seen: list[str] = []
    codes: dict[str, int] = {}

    def fake_run(command, cwd, timeout, grace=5.0):
        seen.append(command)
        return codes.get(command, 0), False

    patch_lib("run_with_timeout", fake_run)
    seen_codes = codes
    return type("Calls", (), {"seen": seen, "codes": seen_codes})()


def test_the_round_test_is_parsed_and_unset_by_default(patch_lib, refactor, monkeypatch):
    assert _parsed_init_args(patch_lib, refactor, monkeypatch)["round_test"] is None
    captured = _parsed_init_args(
        patch_lib, refactor, monkeypatch, "--round-test", "pytest tests -q")
    assert captured["round_test"] == "pytest tests -q"


def test_init_records_the_round_test(run_init, tmp_path, test_calls):
    """AC1 — `--round-test` は状態の `round_test.command` に残る。"""
    run_init(_args(tmp_path, round_test="pytest -q -k scope", baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["round_test"]["command"] == "pytest -q -k scope"
    assert state["round_test"]["status"] == "green"
    assert state["baseline_test"]["command"] == "true"
    assert test_calls.seen == ["true", "pytest -q -k scope"], "全体テストの後に範囲のテストを 1 回"


def test_an_omitted_round_test_is_recorded_as_omitted_and_runs_once(
        run_init, tmp_path, test_calls):
    """AC1 / AC10b — 省けば `round_test.command` は空で、テストの実行は 1 回。

    省いたことを残す。項目の検証は全体のテストから組み立てた語の並びだけを使う。
    """
    run_init(_args(tmp_path, round_test=None, baseline_test="pytest -q"))
    _, state = _state_of(tmp_path)
    assert state["round_test"]["command"] is None
    assert state["round_test"]["status"] == "green"
    assert test_calls.seen == ["pytest -q"]


def test_a_round_test_equal_to_the_baseline_test_runs_once(run_init, tmp_path, test_calls):
    """全体のテストと同じ文字列は 2 度走らせず、省いたときと同じに残す。"""
    run_init(_args(tmp_path, round_test="true", baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["round_test"]["command"] is None
    assert test_calls.seen == ["true"]


@pytest.mark.parametrize("command", ["false", "exit 5"])
def test_init_stops_when_the_round_test_fails(run_init, tmp_path, command):
    """AC4 — 範囲のテストが成功しなければ止める。集まらない終了コード 5 も失敗。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, round_test=command, baseline_test="true"))
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


def test_init_stops_when_the_round_test_runs_outside_the_scope_tests(run_init, tmp_path, test_calls):
    """AC4 — `--scope` のテストの置き場所が `--round-test` の実行集合の外なら止める。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, scope=["src", "tests"],
                       round_test="pytest src", baseline_test="true"))
    assert e.value.code == refactor_abort()
    assert test_calls.seen == [], "関門はテストの実行より先"


def test_init_hints_the_round_test_when_the_baseline_test_is_broad(
        run_init, tmp_path, capsys, test_calls):
    """AC5 — `--round-test` が無く全体を走らせる `--baseline-test` なら、案内して続ける。"""
    run_init(_args(tmp_path, round_test=None, baseline_test="pytest -q"))
    assert "--round-test" in capsys.readouterr().err
    assert _state_path(tmp_path).exists()


def test_init_does_not_hint_when_the_round_test_is_given(run_init, tmp_path, capsys):
    run_init(_args(tmp_path, round_test="true", baseline_test="true"))
    assert "--round-test" not in capsys.readouterr().err


def test_resume_notifies_a_changed_round_test(run_init, tmp_path, capsys):
    """再開では `--round-test` を反映せず、違えば知らせる。"""
    run_init(_args(tmp_path, round_test="true"))
    _, before = _state_of(tmp_path)
    capsys.readouterr()

    run_init(_args(tmp_path, round_test="pytest -q"))
    _, after = _state_of(tmp_path)
    assert "ℹ --round-test は再開では反映しません" in capsys.readouterr().err
    assert after["round_test"] == before["round_test"]


# ---------- 打ち切り（run_with_timeout の timed_out=True）（R2-001） ----------
#
# 現状固定テスト。着手前のテストと範囲のテストが「打ち切り」で止まる 2 経路
# （setup.py の `_run_baseline_test` / `_run_round_test` の timed_out=True）は
# どのテストも通していなかった。失敗（終了コード非 0）とは別の分岐なので、現状の
# 終了コードと、状態ファイルが書かれないことをそのまま記録する。


@pytest.fixture
def timeout_calls(patch_lib):
    """`init` のテスト実行を差し替え、コマンドごとに打ち切り（timed_out=True）へ倒せる。

    `timed_out` に載せたコマンドだけ `(None, True)` を返す。それ以外は `(0, False)`。
    """
    seen: list[str] = []
    timed_out: set[str] = set()

    def fake_run(command, cwd, timeout, grace=5.0):
        seen.append(command)
        if command in timed_out:
            return None, True
        return 0, False

    patch_lib("run_with_timeout", fake_run)
    return type("Calls", (), {"seen": seen, "timed_out": timed_out})()


def test_init_aborts_when_the_baseline_test_times_out(run_init, tmp_path, timeout_calls, capsys):
    """R2-001 — 着手前のテストが打ち切りで止まる経路（setup.py 630-634）。"""
    timeout_calls.timed_out.add("true")
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, baseline_test="true", budget_minutes="10"))
    # 現状固定: `die` の既定の終了コード（打ち切り）。
    assert e.value.code == refactor_abort()
    # 状態ファイルは打ち切りの後の保存に届かないため書かれない。
    assert not _state_path(tmp_path).exists()
    # 出力は文言の完全一致を取らず、打ち切った秒数（予算 10 分の 0.1 = 60）が含まれることだけを見る。
    assert "60" in capsys.readouterr().err


def test_init_aborts_when_the_round_test_times_out(run_init, tmp_path, timeout_calls, capsys):
    """R2-001 — 範囲のテストが打ち切りで止まる経路（setup.py 662-663）。"""
    # 着手前のテストは通し、範囲のテストだけ打ち切る。
    timeout_calls.timed_out.add("pytest -q -k scope")
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, round_test="pytest -q -k scope", baseline_test="true",
                       budget_minutes="10"))
    # 現状固定: 範囲のテストの打ち切りは ABORT。
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()
    # 着手前のテストを通したあと、範囲のテストで止まる順序。
    assert timeout_calls.seen == ["true", "pytest -q -k scope"]
    assert "60" in capsys.readouterr().err


# ---------- 前回の状態の扱い（#933 の AC25 と「再開」） ----------

def _overwrite_state(tmp_path, state):
    path = _state_path(tmp_path)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


def test_an_unfinished_old_state_stops_the_init(run_init, tmp_path, capsys):
    """AC25 — 旧い形（`schema` なし・`rounds` あり）で `final` が空なら止め、状態を変えない。"""
    run_init(_args(tmp_path))
    old = {"id": 130, "rounds": [{"round": 1}], "final": None, "phase": "apply"}
    path = _overwrite_state(tmp_path, old)
    before = path.read_text(encoding="utf-8")
    capsys.readouterr()

    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path))
    assert e.value.code == refactor_abort()
    assert path.read_text(encoding="utf-8") == before


def test_a_finished_old_state_is_rebuilt(run_init, tmp_path):
    """AC25 — 旧い形で `final` が入っていれば、版 2 の形で新しく作り直す。"""
    run_init(_args(tmp_path))
    _overwrite_state(tmp_path, {"id": 130, "rounds": [{"round": 1}],
                                "final": {"status": "approved"}, "phase": "done"})

    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["schema"] == 2
    assert state["phase"] == "propose"
    assert "rounds" not in state and "final" not in state


def test_a_done_state_is_rebuilt(run_init, tmp_path):
    """版 2 で `phase` が `done` なら再開せず、新しく始める。"""
    run_init(_args(tmp_path))
    path, state = _state_of(tmp_path)
    state["phase"] = "done"
    state["candidates"] = [{"id": "C-001"}]
    state["started_at"] = "2000-01-01T00:00:00"
    _overwrite_state(tmp_path, state)

    run_init(_args(tmp_path))
    _, after = _state_of(tmp_path)
    assert after["phase"] == "propose"
    assert after["candidates"] == []
    assert after["started_at"] != "2000-01-01T00:00:00"


def test_resume_emits_the_phase_to_resume_from(run_init, tmp_path, capsys):
    """AC24 — 再開の出力は、状態の `phase` と実装担当をそのまま返す。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    state["phase"] = "implement"
    _overwrite_state(tmp_path, state)
    capsys.readouterr()

    run_init(_args(tmp_path))
    lines = capsys.readouterr().out.splitlines()
    assert "PHASE=implement" in lines
    assert "IMPL=claude" in lines


# ---------- 予算の再開での扱い（設計の「再開」） ----------

def test_resume_before_the_plan_replaces_the_budget(run_init, tmp_path, capsys):
    """計画の前（phase が propose / plan で plan が無い）なら置き換えて記録に積む。"""
    run_init(_args(tmp_path))
    capsys.readouterr()

    run_init(_args(tmp_path, budget_minutes="120"))
    _, state = _state_of(tmp_path)
    assert state["budget_minutes"] == 120
    assert [c["field"] for c in state["resume_changes"]] == ["budget_minutes"]
    assert "budget_minutes: 30 → 120" in capsys.readouterr().err


@pytest.mark.parametrize("phase, plan", [
    ("plan", {"end_at": "2026-09-24T11:00:00"}),
    ("add-tests", {"end_at": "2026-09-24T11:00:00"}),
    ("implement", None),
])
def test_resume_after_the_plan_only_notifies_the_budget(
        run_init, tmp_path, capsys, phase, plan):
    """計画の後は置き換えず、「反映しない」の 1 行だけを出す。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    state["phase"], state["plan"] = phase, plan
    _overwrite_state(tmp_path, state)
    capsys.readouterr()

    run_init(_args(tmp_path, budget_minutes="120"))
    _, after = _state_of(tmp_path)
    assert after["budget_minutes"] == 30
    assert after["resume_changes"] == []
    assert "ℹ --budget-minutes は再開では反映しません" in capsys.readouterr().err


# ---------- 実装担当（#933 の決定 1・AC21） ----------

def test_the_host_implements_by_default(run_init, tmp_path):
    run_init(_args(tmp_path), probe={})
    _, state = _state_of(tmp_path)
    assert (state["implementer"], state["implementer_reason"]) == ("claude", "host")
    assert state["implementer_named"] is None


def test_a_named_implementer_wins(run_init, tmp_path, capsys):
    run_init(_args(tmp_path, implementer="kiro", model=["kiro=claude-opus-5"]), probe={})
    _, state = _state_of(tmp_path)
    assert (state["implementer"], state["implementer_reason"]) == ("kiro", "named")
    assert state["implementer_named"] == "kiro"
    assert state["implementer_model"] == {"requested": "claude-opus-5", "observed": None}
    out = capsys.readouterr().out.splitlines()
    assert "IMPL=kiro" in out and "IMPL_MODEL=claude-opus-5" in out


def test_the_first_participant_implements_when_the_host_is_out(run_init, tmp_path):
    run_init(_args(tmp_path, exclude=[["claude"]]), probe={})
    _, state = _state_of(tmp_path)
    assert (state["implementer"], state["implementer_reason"]) == ("codex", "first")


def test_a_named_implementer_outside_the_participants_stops(run_init, tmp_path):
    """AC21 — 名指しが参加者に無ければ終了コード 4 で止め、状態を作らない。"""
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, implementer="agy"), probe={})
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()


def test_resume_before_the_plan_reassigns_a_dropped_implementer(run_init, tmp_path, capsys):
    """計画の前に実装担当が参加者から外れたら、決め方を当て直して記録に積む。"""
    run_init(_args(tmp_path), probe={})
    capsys.readouterr()

    run_init(_args(tmp_path, exclude=[["claude"]]), probe={})
    _, state = _state_of(tmp_path)
    assert (state["implementer"], state["implementer_reason"]) == ("codex", "first")
    fields = [c["field"] for c in state["resume_changes"]]
    assert fields == ["participants", "implementer"]
    assert state["resume_changes"][1]["from"] == "claude"
    assert "IMPL=codex" in capsys.readouterr().out.splitlines()


def test_resume_after_the_plan_stops_when_the_implementer_is_dropped(run_init, tmp_path):
    """計画の後に実装担当が外れたら終了コード 4 で止め、状態を書き換えない。"""
    run_init(_args(tmp_path), probe={})
    _, state = _state_of(tmp_path)
    state["phase"], state["plan"] = "add-tests", {"end_at": "2026-09-24T11:00:00"}
    path = _overwrite_state(tmp_path, state)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, exclude=[["claude"]]), probe={})
    assert e.value.code == refactor_abort()
    assert path.read_text(encoding="utf-8") == before


def test_resume_keeps_the_implementer_when_it_stays(run_init, tmp_path):
    """実装担当が参加者に残るなら、作り直しても替えない。"""
    run_init(_args(tmp_path), probe={})
    run_init(_args(tmp_path, exclude=[["kiro"]]), probe={})
    _, state = _state_of(tmp_path)
    assert state["implementer"] == "claude"
    assert [c["field"] for c in state["resume_changes"]] == ["participants"]


# ---------- Jev を使うかの判定（#933 の決定 2・AC22 AC23） ----------

def test_without_the_key_the_judge_is_the_runtime(run_init, tmp_path):
    """鍵が無ければ Jev を使わない（conftest が鍵を外している）。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["judge"] == {"kind": "runtime", "reason": "no_key", "failures": 0}


def test_a_private_repository_does_not_use_jev(run_init, tmp_path, monkeypatch, cmd_setup):
    """AC23 — 鍵があっても、公開でない（判定できないを含む）リポジトリへは問わない。

    公開かの判定だけを差し替える。疎通の問いへは進まないため HTTP は呼ばれない。
    """
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "dummy")
    monkeypatch.delenv("NDF_JEV", raising=False)
    asked: list[str] = []
    monkeypatch.setattr(cmd_setup, "_repo_is_public",
                        lambda repo: asked.append(repo) or None)
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert asked == ["acme/demo"]
    assert state["judge"]["kind"] == "runtime"
    assert state["judge"]["reason"] == "private_repo"


def test_the_judge_decision_is_recorded_once(run_init, tmp_path, monkeypatch, cmd_setup):
    """判定は初期化で 1 度だけ行い、再開では問い直さない。"""
    calls: list[int] = []

    def fake_decide(is_public, env=None, **_):
        calls.append(1)
        return {"kind": "jev", "reason": None, "failures": 0}

    monkeypatch.setattr(cmd_setup.jev, "decide", fake_decide)
    run_init(_args(tmp_path))
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["judge"] == {"kind": "jev", "reason": None, "failures": 0}
    assert calls == [1]


# ---------- フェーズの開始（`start-phase`。#933 の決定 8・実装計画 I1） ----------

@pytest.fixture
def phase_state(tmp_path, env_tmp_dir, monkeypatch, cmd_phases):
    """版 2 の状態と、`rev-parse HEAD` が通る作業ディレクトリを用意する。

    時計は `set_now` で差し替える。監視の上限の環境変数は外す（表の値で比べる）。
    """
    import datetime as dt
    from crossref_helpers import make_state_v2

    work = tmp_path / "work"
    work.mkdir()
    run_git("init", "-q", cwd=work)
    run_git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-q", "--allow-empty", "-m", "init", cwd=work)
    path = make_state_v2(tmp_path, work, started_at="2026-09-24T10:00:00+09:00")
    env_tmp_dir(path)
    for name in ("MONITOR_TIMEOUT", "MONITOR_TIMEOUT_CLAUDE"):
        monkeypatch.delenv(name, raising=False)

    tz = dt.timezone(dt.timedelta(hours=9))
    current = {"now": dt.datetime(2026, 9, 24, 10, 0, 0, tzinfo=tz)}
    monkeypatch.setattr(cmd_phases.statefile, "now",
                        lambda: current["now"].replace(tzinfo=None).isoformat(timespec="seconds"))
    monkeypatch.setattr(cmd_phases.clock, "now", lambda: current["now"])

    def set_now(**delta):
        current["now"] = dt.datetime(2026, 9, 24, 10, 0, 0, tzinfo=tz) + dt.timedelta(**delta)

    def edit(**fields):
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(fields)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    return types.SimpleNamespace(path=path, work=work, set_now=set_now, edit=edit, tz=tz)


@pytest.fixture
def start_phase(phase_state, cmd_phases, capsys):
    """`start-phase` を呼び、`(状態, PHASE_TIMEOUT の値)` を返す。"""
    def _run(phase):
        capsys.readouterr()
        cmd_phases.cmd_start_phase(types.SimpleNamespace(id=130, phase=phase))
        out = capsys.readouterr().out.splitlines()
        # 値は `shlex.quote` を通る（空は `''`）。呼び出し側の `eval` と同じに読む。
        timeout = next(shlex.split(line.split("=", 1)[1]) or [""]
                       for line in out if line.startswith("PHASE_TIMEOUT="))[0]
        return json.loads(phase_state.path.read_text(encoding="utf-8")), timeout
    return _run


def test_start_phase_records_the_start_once(phase_state, start_phase):
    """2 度目の起動で起点を書き換えない。所要は最初の起動から測る。"""
    state, _ = start_phase("propose")
    first = state["phases"]["propose"]
    assert first["started_at"] == "2026-09-24T10:00:00"
    assert first["base_sha"]
    assert state["phase"] == "propose"

    phase_state.set_now(minutes=5)
    state, _ = start_phase("propose")
    again = state["phases"]["propose"]
    assert (again["started_at"], again["launch_started_at"], again["base_sha"]) == (
        first["started_at"], first["launch_started_at"], first["base_sha"])
    # 上限は起動のたびに残りから出し直す（再開の起動が枠を越えない）
    assert again["timeout"] == first["timeout"] - 300


def test_start_phase_rewrites_the_launch_start_for_fix(phase_state, start_phase):
    """修正だけは起動のたびに `launch_started_at` を書き直す。`started_at` は最初のまま。"""
    state, _ = start_phase("fix")
    assert state["phases"]["fix"]["launch_started_at"] == "2026-09-24T10:00:00"

    phase_state.set_now(minutes=7)
    state, _ = start_phase("fix")
    record = state["phases"]["fix"]
    assert record["started_at"] == "2026-09-24T10:00:00"
    assert record["launch_started_at"] == "2026-09-24T10:07:00"


# 予算 60 分・開始 10:00。余裕は 0.05·B = 180 秒（決定 24）。
PLANNED = {
    "plan": {"reserve": {"danger_whole_test": 1.0, "final_whole_test": 1.0, "fix": 5.5}},
    "items": [{"start_deadline": "2026-09-24T10:30:00+09:00",
               "test_start_deadline": "2026-09-24T10:20:00+09:00",
               "estimate": {"test": 3.0, "implement": 2.0, "verify": 0.2}}],
}


@pytest.mark.parametrize("phase, planned, expected", [
    ("propose", False, 12 * 60 + 180),       # 提案の枠の終わり 10:12
    ("plan", False, 18 * 60 + 180),          # 計画の枠の終わり 10:18
    ("add-tests", True, 23 * 60 + 180),      # 最後の項目の完了の締め切り 10:20 + 3 分
    ("implement", True, 32 * 60 + 180),      # 10:30 + 2 分
    ("fix", True, 58 * 60 + 180),            # 開始 + 60 − 全体のテストの控え 2 分
    ("final-fix", False, 60 * 60 + 180),     # 想定最大時間の終わり 11:00
])
def test_start_phase_returns_the_time_left_to_the_end_of_the_phase(
        phase_state, start_phase, phase, planned, expected):
    """決定 23・24: 監視の上限は、その段の終わりまでの残り + 余裕。CLI の上限は + 余裕。"""
    if planned:
        phase_state.edit(**PLANNED)
    state, timeout = start_phase(phase)
    assert timeout == str(expected)
    record = state["phases"][phase]
    assert record["timeout"] == expected and record["cli_timeout"] == expected + 180


def test_start_phase_for_the_final_fix_keeps_the_phase(phase_state, start_phase):
    """最終ゲートの修正は状態の段を変えない（再開の地点が狂う）。"""
    phase_state.edit(phase="final")
    state, _ = start_phase("final-fix")
    assert state["phase"] == "final"


@pytest.mark.parametrize("phase", ["add-tests", "implement", "fix"])
def test_start_phase_without_a_plan_returns_no_timeout(phase_state, start_phase, phase):
    _, timeout = start_phase(phase)
    assert timeout == ""


def test_start_phase_rejects_an_unknown_phase(phase_state, cmd_phases):
    with pytest.raises(SystemExit) as e:
        cmd_phases.cmd_start_phase(types.SimpleNamespace(id=130, phase="review"))
    assert e.value.code == refactor_abort()
