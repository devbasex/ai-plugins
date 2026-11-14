"""worktree の後片付け・追従・ローカル検証・テスト環境の判断（純粋な関数）と、書き込み先の推定。"""

from __future__ import annotations

import pytest

from ndf.domains.worktree import localenv_decide, merged_decide, session_decide, testenv_decide, write_target

SELECTED = {"promotion": "selected", "production_branch": "main"}


@pytest.mark.parametrize(
    ("branch", "flow", "contained", "kept"),
    [
        ("feat/a", SELECTED, False, False),  # スプリントブランチでなければ残さない
        ("sprint/s1", SELECTED, False, True),  # 選んで集める取り込み方では本番に含まれるまで残す
        ("sprint/s1", SELECTED, True, False),
        ("sprint/s1", SELECTED, None, True),  # 含まれるかを確かめられなければ残す
        ("sprint/s1", {"promotion": "whole", "production_branch": "main"}, False, False),
        ("sprint/s1", None, False, False),  # 記録も宣言も無ければ既定の行（whole）
        ("sprint/s1", {"promotion": "nope", "production_branch": "main"}, True, True),  # 表に無い取り込み方は残す
        ("sprint/s1", {"promotion": "selected"}, False, False),  # 本番チャネルが無ければ比べる先が無い
    ],
)
def test_sprint_keep(branch: str, flow: dict | None, contained: bool | None, kept: bool) -> None:
    assert (merged_decide.sprint_keep(branch, flow, contained, "origin") is not None) == kept


def test_long_lived_keep() -> None:
    assert merged_decide.long_lived_keep("develop", "develop", "main") is not None  # リリースの PR の head はベースブランチ
    assert merged_decide.long_lived_keep("main", "develop", "main") is not None
    assert merged_decide.long_lived_keep("feat/a", "develop", "main") is None
    assert merged_decide.long_lived_keep("feat/a", None, None) is None


def test_pull_skip() -> None:
    assert merged_decide.pull_skip("develop", "develop") is None
    assert "detached" in str(merged_decide.pull_skip(None, "develop"))
    assert merged_decide.pull_skip("develop", None) is not None


@pytest.mark.parametrize(
    ("dirty", "branches", "want"),
    [
        (True, ["feat/a"], (session_decide.FOLLOW_STAY, None)),
        (False, ["feat/a", None], (session_decide.FOLLOW_DETACH, "feat/a")),
        (False, ["feat/a", "feat/b"], (session_decide.FOLLOW_BASE, None)),
        (False, [], (session_decide.FOLLOW_BASE, None)),
    ],
)
def test_follow_target(dirty: bool, branches: list, want: tuple) -> None:
    assert session_decide.follow_target(dirty, branches) == want


def test_localenv_verify_separates_not_running_from_mismatch() -> None:
    assert localenv_decide.verify(" feat/a\n", "feat/a").result == localenv_decide.MATCH
    assert localenv_decide.verify("feat/b", "feat/a").result == localenv_decide.MISMATCH
    assert localenv_decide.verify(None, "feat/a").result == localenv_decide.NOT_RUNNING
    assert localenv_decide.verify("feat/a", None).result == localenv_decide.NOT_RUNNING


def test_localenv_mode() -> None:
    verdict, hits = localenv_decide.mode(["db/migrations/1.sql", "src/a.py", "db/x.sql"], ["db/migrations/*", "*.sql"])
    assert verdict.result == localenv_decide.ISOLATE
    assert hits == ["db/migrations/1.sql（db/migrations/*）", "db/x.sql（*.sql）"]
    assert localenv_decide.mode(["src/a.py"], ["*.sql"])[0].result == localenv_decide.SHARE
    assert localenv_decide.mode(["x.sql"], [])[0].result == localenv_decide.SHARE


def test_env_name_keeps_digest_and_length() -> None:
    a = testenv_decide.env_name("My_Repo", "feat/" + "x" * 80)
    b = testenv_decide.env_name("My_Repo", "feat/" + "x" * 80 + "y")
    assert len(a) <= testenv_decide.NAME_MAX and a.startswith("my-repo-wt-feat-x")
    assert a != b  # 先頭が同じ長い名前どうしでも要約値で分かれる
    assert testenv_decide.env_name("r", "feat/a") == testenv_decide.env_name("r", "feat/a")


def test_ports_and_slots() -> None:
    assert testenv_decide.ports([20000, 30000], 2, {"http": 1, "db": 2}) == {"http": 20041, "db": 20042}
    assert testenv_decide.ports(None, 2, {"http": 1}) == {}
    with pytest.raises(testenv_decide.PortsError):
        testenv_decide.ports([20000, 20030], 2, {"http": 1})
    assert testenv_decide.free_slot([0, 1, 3]) == 2
    assert testenv_decide.free_slot(range(testenv_decide.SLOTS)) is None


def test_compose_values_and_volumes() -> None:
    got = testenv_decide.compose_values("e", 3, "/w", "net", {"http-api": 20061})
    assert got == {"NDF_ENVIRONMENT": "e", "NDF_SLOT": "3", "NDF_WORKTREE": "/w", "NDF_SHARED_NETWORK": "net", "NDF_PORT_HTTP_API": "20061"}
    assert testenv_decide.discards_volumes(None) and testenv_decide.discards_volumes(True)
    assert not testenv_decide.discards_volumes(False) and not testenv_decide.discards_volumes("yes")


@pytest.mark.parametrize(
    ("command", "want"),
    [
        ("echo a > out.txt", ["/r/out.txt"]),
        ("cd sub && echo x > f", ["/r/sub/f"]),
        ("make && cd x; echo y > f", []),  # 条件つきの cd の後は決めない
        ("cd a || exit 1; echo > f", ["/r/a/f"]),
        ("a || cd b; echo > f", []),
        ("(cd s; echo > a); echo > b", ["/r/s/a", "/r/b"]),
        ("for i in 1; do cd $i; done; echo > f", []),
        ("f() { cd x; }; f; echo > q", []),
        ("echo $(cd z; echo > q) > f", ["/r/z/q", "/r/f"]),
        ("sed -i -e 's/a/b/' x y", ["/r/x", "/r/y"]),
        ("sed -n p f", []),
        ("cp -t d a b", ["/r/d"]),
        ("mv a b c", ["/r/c"]),
        ("echo x | tee -a 'q r' > y", ["/r/q r", "/r/y"]),
        ("echo > $HOME/x 2>&1 >/dev/null", []),
        ("cat <<EOF > o\nhi\nEOF", ["/r/o"]),
        ("echo 'unterminated", []),
    ],
)
def test_shell_targets(command: str, want: list[str]) -> None:
    assert write_target.shell_targets(command, "/r") == want


def test_shell_targets_without_base_and_patch() -> None:
    assert write_target.shell_targets("cd x; echo > ../f") == ["../f"]
    assert write_target.normalize_path("/r/a/../../outside", "/") == "/outside"
    assert write_target.patch_targets("*** Update File: a.py\n*** Move to: b.py\n+x") == ["a.py", "b.py"]


# 現状固定（I-001）: `_Scan.redirects` を通る経路。期待値は今の実装の出力。
@pytest.mark.parametrize(
    ("command", "want"),
    [
        ("echo a >> o1", ["/r/o1"]),
        ("echo a &> o2", ["/r/o2"]),
        ("echo a &>> o3", ["/r/o3"]),
        ("echo a >| o4", ["/r/o4"]),
        ("cat <> o5", []),  # NOTE: 現状固定。`<>` は書き込み先に出ない
        ("echo a >&2", []),
        ("echo a >& -", []),
        ("echo a >&2 >& - >& f", ["/r/f"]),
        ("echo a 2> err < in", ["/r/err"]),
        ("echo a > /dev/stderr", []),
        ('echo a > "$X"', []),
        ("echo a > 'lit'", ["/r/lit"]),
        ("echo a > ~u/h", []),
        ("{ echo a; } > grp", ["/r/grp"]),
        ("while true; do echo; done > loop", ["/r/loop"]),
        ("{ echo a; echo b; } 2> e > o", ["/r/e", "/r/o"]),
        ("{ cd s; echo; } > o", ["/r/o"]),  # 複合コマンドのリダイレクトは入る前の位置で開く
        ("cat <<EOF 2> e > o\nhi\nEOF", ["/r/e", "/r/o"]),
        ("cd s; cat <<EOF > o\nhi\nEOF", ["/r/s/o"]),
        ("cat <<EOF | tee t\nhi\nEOF", []),  # NOTE: 現状固定。ヒアドキュメントの後のパイプは読み切れない
        ("echo a; echo b > c", ["/r/c"]),
        ('echo a > "q $(cd z; echo > in) r"', ["/r/z/in"]),
        ("echo a > <(cat > ps)", ["/r/ps", "/r/<(cat > ps)"]),  # NOTE: 現状固定。置換の字面も出る
    ],
)
def test_shell_targets_redirects_characterization(command: str, want: list[str]) -> None:
    assert write_target.shell_targets(command, "/r") == want


def test_shell_targets_redirects_home_and_no_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/h")
    assert write_target.shell_targets("echo a > ~/h", "/r") == ["/home/h/h"]
    assert write_target.shell_targets("cat <<EOF 2> e > o\nhi\nEOF") == ["e", "o"]
    assert write_target.shell_targets("{ cd s; echo; } > o") == ["o"]


# 現状固定（I-013）: `_Scan._andor` を通る `&&`・`||` の組み合わせ。期待値は今の実装の出力。
@pytest.mark.parametrize(
    ("command", "want"),
    [
        ("cd a && echo > f", ["/r/a/f"]),
        ("cd a && cd b && echo > f", ["/r/a/b/f"]),
        ("true && cd a; echo > f", []),  # 条件つきの cd
        ("cd a && true; echo > f", ["/r/a/f"]),
        ("cd a || exit; echo > f", ["/r/a/f"]),
        ("cd a || return 1; echo > f", ["/r/a/f"]),
        ("cd a || break; echo > f", ["/r/a/f"]),
        ("cd a || continue; echo > f", ["/r/a/f"]),
        ("cd a || { echo no; exit 1; }; echo > f", ["/r/a/f"]),
        ("cd a || { echo no; }; echo > f", []),
        ("cd a && cd b || exit; echo > f", ["/r/a/b/f"]),
        ("cd a || cd b; echo > f", []),
        ("true || cd b; echo > f", []),
        ("true || true; echo > f", ["/r/f"]),
        ("true || exit; echo > f", ["/r/f"]),
        ("cd a || echo > g", ["/r/g"]),  # 右辺は cd の前の位置で走る
        ("cd a && echo > g || echo > h", ["/r/a/g"]),
        ("cd a || exit || true; echo > f", ["/r/a/f"]),
        ("cd a || exit 1 && echo > f", ["/r/a/f"]),
        ("a && b || cd c; echo > f", []),
        ("cd a > o || exit; echo > f", ["/r/o", "/r/a/f"]),
        ("cd /x && echo > f", ["/x/f"]),
        ("cd a && echo > f && cd b && echo > g", ["/r/a/f", "/r/a/b/g"]),
        ("f1 && f2; echo > f", ["/r/f"]),
        ("cd a && (cd b) ; echo > f", ["/r/a/f"]),
    ],
)
def test_shell_targets_andor_characterization(command: str, want: list[str]) -> None:
    assert write_target.shell_targets(command, "/r") == want
