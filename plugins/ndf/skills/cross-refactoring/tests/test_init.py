"""`init` のテスト。

`gh` は呼ばないので `sh` を差し替える。git は実際に動かし、
**書き込み用の作業ディレクトリが本当に作れるか**を確かめる。
"""
from __future__ import annotations

import sys
import json
import os
import pathlib
import subprocess
import types

import pytest


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
        # **テストの置き場所を含める**（#436 決定 5）。含めないと `init` の関門で
        # 止まる。関門そのものは `test_scope_gate.py` で見る。
        "pr": 130, "scope": ["src", "tests"], "host": "claude",
        "max_outer_rounds": 3, "max_fix_rounds": 3, "max_items_per_round": 5,
        "max_test_rounds": 2, "ci_check": None, "workflow_step": False,
        "severity_threshold": "minor", "model": None, "baseline_test": "true",
        "sync_command": None, "plan_file": None,
        "test_timeout": 60,
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
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, scope=["src", "tests"],
                       baseline_test="pytest src/unit"))


def test_init_checks_the_scope_before_running_the_baseline_test(run_init, tmp_path):
    """関門は着手前のテストより先に通す。落ちる実行に時間を使わせない。"""
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, scope=["src"], baseline_test="false"))


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


def test_the_round_caps_are_unset_in_the_arguments(patch_lib, refactor, monkeypatch):
    """引数の既定は未指定で、再開で「渡さなかった」と読める（#727 の決定 13）。"""
    captured = _parsed_init_args(patch_lib, refactor, monkeypatch)
    for key in ("max_test_rounds", "max_outer_rounds", "max_fix_rounds",
                "max_items_per_round", "test_timeout", "severity_threshold",
                "workflow_step", "include", "exclude", "require_all"):
        assert captured[key] is None, key


def test_a_new_run_fills_the_round_caps_with_their_defaults(run_init, tmp_path):
    """E1 — 4 つの上限は別々の単位に掛かる（#436 決定 8）。

    新規の初期化が現行の既定（提案 3 / テスト整備 2 / 修正 3 / 採用 5）へ置き換える。
    """
    run_init(_args(tmp_path, max_outer_rounds=None, max_test_rounds=None,
                   max_fix_rounds=None, max_items_per_round=None,
                   test_timeout=None, severity_threshold=None, workflow_step=None))
    _, state = _state_of(tmp_path)
    assert (state["max_outer_rounds"], state["max_test_rounds"],
            state["max_fix_rounds"], state["max_items_per_round"]) == (3, 2, 3, 5)
    assert state["test_timeout"] == 900
    assert state["severity_threshold"] == "minor"
    assert state["workflow_step"] is False


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


def test_init_starts_with_a_test_round(run_init, tmp_path):
    """B4 — 初期化の後、最初の提案ラウンドの前にテスト整備ラウンドを置く。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["round_kind"] == "test"
    assert state["max_test_rounds"] == 2
    assert state["test_vocabulary"]["cases"], "語彙は状態ファイル経由で渡す"


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
    first["rounds"].append({"round": 1, "impl": "codex"})
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

    run_init(_args(tmp_path, model=["codex=gpt-4"]))
    _, second = _state_of(tmp_path)
    assert second["models"]["codex"] == "gpt-5.5", "指定値が上書きされている"
    assert len(second["rounds"]) == 1, "状態が作り直されている"


def test_init_emits_shell_assignments(run_init, tmp_path, capsys):
    run_init(_args(tmp_path))
    out = capsys.readouterr().out
    assert "RUNTIMES_CSV=claude,codex,kiro" in out
    # 空白を含む値は必ず引用する。引用しないと呼び出し側の eval で語が割れる。
    assert "RUNTIMES='claude codex kiro'" in out
    assert "IMPL_POOL=" not in out
    assert "TMP_DIR=" in out and "WORK=" in out


def test_init_emits_the_stall_timeout_for_the_implementer(run_init, tmp_path, capsys):
    """AC40: 無進捗の許容は、テストの制限時間に 900 秒を足した値である。

    適用と修正の担当はテストを 1 回実行し、その間は何も出力しない。制限時間
    そのままでは実行中に打ち切られる。
    """
    args = _args(tmp_path)
    args.test_timeout = 900          # `--test-timeout` の既定

    run_init(args)

    assert "IMPL_STALL_TIMEOUT=1800" in capsys.readouterr().out


def test_the_stall_timeout_follows_the_test_timeout(run_init, tmp_path, capsys):
    """AC40: テストの制限時間を変えると、無進捗の許容も一緒に動く。"""
    args = _args(tmp_path)
    args.test_timeout = 1200

    run_init(args)

    assert "IMPL_STALL_TIMEOUT=2100" in capsys.readouterr().out


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

@pytest.mark.parametrize("arg, value", [
    ("max_outer_rounds", 5), ("max_test_rounds", 4),
    ("max_fix_rounds", 6), ("max_items_per_round", 8),
])
def test_resume_reflects_a_changed_cap(run_init, tmp_path, capsys, arg, value):
    """AC38 — 上限は再開で渡せば反映し、`旧 → 新` を 1 行出し、記録に 1 件積む。"""
    run_init(_args(tmp_path))
    _, before = _state_of(tmp_path)
    capsys.readouterr()

    run_init(_args(tmp_path, **{arg: value}))
    _, after = _state_of(tmp_path)
    assert after[arg] == value
    assert f"{arg}: {before[arg]} → {value}" in capsys.readouterr().err
    assert [c["field"] for c in after["resume_changes"]] == [arg]


@pytest.mark.parametrize("over, option", [
    ({"model": ["codex=x"]}, "--model"),
    ({"host": "codex"}, "--host"),
    ({"scope": ["other", "tests"]}, "--scope"),
    ({"baseline_test": "pytest -q"}, "--baseline-test"),
    ({"severity_threshold": "major"}, "--severity-threshold"),
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
    for key in ("models", "host", "target_scope", "baseline_test", "severity_threshold"):
        assert after[key] == before[key], key
    assert after["resume_changes"] == []


def test_resume_without_arguments_changes_nothing(run_init, tmp_path, capsys):
    """AC39 — 何も渡さない再開では、上限・モデル・参加者が変わらず、確認もしない。"""
    run_init(_args(tmp_path), probe={})
    _, before = _state_of(tmp_path)

    run_init(_args(tmp_path), probe={"kiro": "Not logged in"})
    _, after = _state_of(tmp_path)
    assert run_init.probed == [], "担当に関わる引数を渡していないのに確かめ直している"
    for key in ("max_outer_rounds", "max_test_rounds", "max_fix_rounds",
                "max_items_per_round", "models", "runtimes", "participants"):
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
        run_init(_args(tmp_path, exclude=[["kiro"]], max_outer_rounds=9),
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


def test_an_omitted_round_test_is_the_baseline_test_and_runs_once(run_init, tmp_path, test_calls):
    """AC1 — 省けば `baseline_test` と同じコマンドで、テストの実行は 1 回。"""
    run_init(_args(tmp_path, baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["round_test"]["command"] == state["baseline_test"]["command"] == "true"
    assert state["round_test"]["status"] == "green"
    assert test_calls.seen == ["true"]


def test_a_round_test_equal_to_the_baseline_test_runs_once(run_init, tmp_path, test_calls):
    run_init(_args(tmp_path, round_test="true", baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["round_test"]["command"] == "true"
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


def test_init_hints_the_round_test_when_the_baseline_test_is_broad(run_init, tmp_path, capsys):
    """AC5 — `--round-test` が無く全体を走らせる `--baseline-test` なら、案内して続ける。"""
    run_init(_args(tmp_path, baseline_test="true"))
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
        run_init(_args(tmp_path, baseline_test="true", test_timeout=60))
    # 現状固定: `die` の既定の終了コード（打ち切り）。
    assert e.value.code == refactor_abort()
    # 状態ファイルは打ち切りの後の保存に届かないため書かれない。
    assert not _state_path(tmp_path).exists()
    # 出力は文言の完全一致を取らず、打ち切った秒数（60）が含まれることだけを見る。
    assert "60" in capsys.readouterr().err


def test_init_aborts_when_the_round_test_times_out(run_init, tmp_path, timeout_calls, capsys):
    """R2-001 — 範囲のテストが打ち切りで止まる経路（setup.py 662-663）。"""
    # 着手前のテストは通し、範囲のテストだけ打ち切る。
    timeout_calls.timed_out.add("pytest -q -k scope")
    with pytest.raises(SystemExit) as e:
        run_init(_args(tmp_path, round_test="pytest -q -k scope", baseline_test="true",
                       test_timeout=60))
    # 現状固定: 範囲のテストの打ち切りは ABORT。
    assert e.value.code == refactor_abort()
    assert not _state_path(tmp_path).exists()
    # 着手前のテストを通したあと、範囲のテストで止まる順序。
    assert timeout_calls.seen == ["true", "pytest -q -k scope"]
    assert "60" in capsys.readouterr().err
